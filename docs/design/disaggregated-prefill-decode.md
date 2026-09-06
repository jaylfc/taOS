# Disaggregated prefill/decode (spec addendum, GH #150)

## The idea

Prefill and decode have wildly different resource profiles. Prefill is
compute-bound (fat matrix multiplies over the prompt) and benefits
from high FLOPs. Decode is bandwidth-bound (one token at a time,
single KV read) and benefits from high memory bandwidth and low
latency. Running both phases on the same hardware is a compromise.

Disaggregated serving runs prefill on one worker and decode on
another. The prefill worker produces a KV cache blob, ships it to the
decode worker, and the decode worker streams tokens to the client.
This is the architecture behind Splitwise (Microsoft, MLSys 2024),
DistServe, and more recently vLLM's own disaggregation work.

TAOS is a heterogeneous mesh, so we are well-placed to exploit this:
a gaming RTX card is a great decoder, a big Hopper rental is a great
prefiller, and a mid-range NPU is a great... nothing, but you get the
idea. Pair hardware by role, not by "same box".

## Scope

- What roles and role-assignment policy look like
- Protocol sketch for KV handoff
- When it is actually worth the network round-trip
- What infra we are missing

## Non-goals

- **Intra-node multi-stream.** This is about cross-worker
  disaggregation. Single-node continuous batching is a vLLM concern
  we get for free.
- **Load balancing across multiple prefillers.** v1 picks one prefill
  worker and one decode worker per request.
- **Quality guarantees under partial failure.** If the decoder dies
  mid-stream, the request fails. No token-level replay.

## Role model

Every LLM-capable worker advertises a role set:

```
roles: [prefill, decode]          # can do either, default
roles: [prefill]                  # prefill only (big GPU, no NVLink peers)
roles: [decode]                   # decode only (cheap high-VRAM card, low TDP)
```

Roles are set by the user at install time, or derived from hardware
profile (big GPU → both, small GPU → decode only). The scheduler
consults roles when dispatching.

## Dispatch

```
client -> controller
  request: { capability: llm-chat, model: qwen3.5-9b }

controller -> scheduler.pick_disagg_pair(model)
  returns: (prefill_worker, decode_worker)

controller -> prefill_worker
  {model, prompt} -> KV cache blob

prefill_worker -> decode_worker (direct)
  KVHandoff { kv_blob, model, session_id }

decode_worker -> client (stream)
  tokens...
```

If the two workers are the same box (cluster with a single big GPU),
the handoff is a no-op and the scheduler falls back to the colocated
path. Disaggregation is an optimisation, not a requirement.

## KV handoff protocol

Similar to peer-VRAM KV cache but one-shot rather than on-demand:

```
KVHandoff {
  session_id: uuid
  model_uuid: bytes[16]
  layer_count: u32
  pages: list<KVPage>
  token_count: u32
  ttl_seconds: u32
}
```

The blob carries every page for every layer, already compressed in
whatever KV quant the cluster is using. The decode worker stores it
in session-local VRAM and starts streaming tokens as soon as the last
page arrives.

Transport: same HTTP/2 worker-to-worker channel as peer-VRAM. The two
features share the wire format intentionally — KVHandoff is just
`N × KVMove` bundled.

## When it is worth it

Disaggregation pays off when:

1. The prefill phase is >5× longer than a single decode step
2. The network round-trip for the KV handoff is <20% of prefill time
3. The decode worker would otherwise be idle waiting for prefill

For a 9B model at 8K prompt on a 4090 + a 3060, prefill is ~120ms
and decode is ~56 t/s. The KV blob at Q8/T3 is ~400MB. Over 2.5GbE
that's ~1.3 seconds to transfer. Not worth it.

For a 70B model at 32K prompt where prefill is 10+ seconds and the
decode card has to be the bigger one because it's the only card with
the VRAM, it's much more promising.

v1 ships this as **opt-in per agent**, with a scheduler gate that
refuses to use it unless the cluster has the right bandwidth and
workload profile.

## Failure modes

| Failure | Effect | Recovery |
|---|---|---|
| Prefill fails | Request fails | Client retries; scheduler may colocate next time |
| KV handoff truncated | Decode produces garbage | Checksum on the blob, fail fast, re-prefill |
| Decode worker crashes | Stream breaks | Client sees closed stream; partial tokens already delivered |
| Prefill + decode model mismatch | Decode produces garbage | Model uuid check on handoff — fail fast |

Partial failures don't get replayed. The request just fails. This
matches the existing controller behaviour for any mid-stream error.

## What we're missing

- **Session-scoped KV storage on the decode side.** The existing
  llama-cpp backend manages KV per-request, not per-session. We need
  to pin a KV blob in VRAM for the lifetime of a streaming response.
- **Direct worker-to-worker HTTP/2 links.** Same dependency as
  peer-VRAM. Today everything transits the controller.
- **A scheduler policy for picking the pair.** Not trivial: you want
  to pick the prefill worker with the lowest queue depth and the
  decode worker with the most free VRAM, subject to bandwidth
  constraints between them.
- **Per-backend handoff support.** llama-cpp needs a "export KV cache"
  API; vLLM has one via their disaggregation branch; ollama has none.

All of the above are bigger lifts than disaggregation itself. This is
a long-tail feature, not v0.3 material.

## When to revisit

Revisit this spec when:

1. Peer-VRAM KV cache (#149) lands, because they share infrastructure
2. vLLM disaggregation lands upstream and ships in a tagged release
3. A user with heterogeneous hardware asks for it (data point, not
   assumption)
4. We have models >30B running on the cluster regularly — below that,
   colocated is fine

Until then, keep this spec as a design reference and track the
upstream work.

## Tracking

GH #150. Referenced by #149 (shared wire format) and #176 (shared
worker-to-worker transport work).
