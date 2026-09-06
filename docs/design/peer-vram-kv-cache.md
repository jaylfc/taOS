# Peer-VRAM KV cache (spec addendum, GH #149)

## The problem

A single GPU runs out of VRAM long before the model runs out of useful
context. A 9B model on a 12GB card tops out around 786K tokens with
aggressive TurboQuant (see the v0.2 benchmarks). Once a user has two
boxes on the LAN, a 4060Ti and a 3090, the 3090 is sitting half-idle
while the 4060Ti suffocates on KV cache. Peer-VRAM KV cache is the
scheduler's answer: **offload overflow KV pages to the peer worker
over the LAN and pull them back on demand**.

## Scope

This is a design addendum, not a full implementation plan. It covers:

- Motivating scenarios and non-goals
- Protocol sketch: how KV pages get owned, moved and fetched
- Failure modes and recovery
- What has to be true for it to be worth building

## Non-goals

- **Distributed inference.** We are not splitting the forward pass. The
  GPU that holds the model runs every layer. Peer-VRAM only ships the
  KV cache pages.
- **Cross-vendor transfer.** CUDA ↔ ROCm ↔ Metal is out of scope for
  v1. Peer-VRAM requires the same CUDA toolchain on both ends.
- **Transparent mounting.** Not NBD, not RDMA-over-IP. We move pages
  with explicit messages; the rest of the stack treats the KV cache
  as local.

## Protocol sketch

Every KV page has an owner: the host where it was written. When the
owning host runs low on VRAM, the scheduler picks the coldest pages
(by last-access timestamp) and issues `KVMove` to a peer with free
VRAM. The message carries:

- Model id + layer index + page id (so the fetcher can reconstitute
  position)
- The compressed page bytes (TurboQuant already takes care of this)
- A TTL so the peer can evict if it runs low itself

On the fetch side, the runtime stores the page in a per-peer LRU, and
returns it to the owner on `KVFetch(model, layer, page)`. The owner
blocks on the fetch during generation, so there's a round-trip cost
per fetched page. Local pages always win the dispatch race.

Key detail: **only overflow pages move**. The scheduler never ships
pages that fit in local VRAM, because the fetch round-trip is pure
latency. The scheduler only starts evicting to peers once local VRAM
drops below a watermark (e.g. 90% used).

## Wire format

```
KVMove {
  model_uuid: bytes[16]
  layer: u32
  page_id: u32
  quant: enum(fp16, q8, t3, t2, t2_2)
  k_bytes: bytes
  v_bytes: bytes
  ttl_seconds: u32
}

KVFetch {
  model_uuid: bytes[16]
  layer: u32
  page_id: u32
}

KVFetchResponse {
  found: bool
  k_bytes: bytes
  v_bytes: bytes
}
```

Transport: HTTP/2 over the existing worker mesh. No new ports. The
existing controller-signed JWT the worker uses for heartbeats is the
auth token.

## Failure modes

| Failure | Effect | Recovery |
|---|---|---|
| Peer offline during fetch | Request stalls | Fall back: re-prefill the lost pages from the prompt, cap cost at full prefill |
| Peer full, evicts page | `KVFetchResponse.found = false` | Same as above — re-prefill the range |
| Network partition mid-gen | Active request fails | Scheduler retries on local only; stops using peer for that session |
| Owner crashes during gen | All pages lost | Stateless anyway, client re-runs request |

The re-prefill fallback is the safety net. As long as we never **lose
correctness**, peer-VRAM is just a VRAM capacity feature with variable
latency. The worst case is "slower than if you had just bought a
bigger card".

## When it's worth building

Peer-VRAM KV cache is worth building when:

1. Two or more workers on the mesh have asymmetric VRAM (12GB + 24GB)
2. LAN is at least 2.5GbE (gigabit is marginal, WiFi is a bad idea)
3. User wants to run the *same* model on longer context than the
   smaller card can hold, rather than a bigger model

It is not worth building when:

1. Workers have uniform VRAM (just rebalance)
2. LAN is gigabit or worse (fetch latency dominates)
3. User can buy a bigger card for less than the dev cost

For v1, ship it as an opt-in feature under an agent-level flag:
`peer_vram_kv_cache: true`. Default off. Surface it in the deploy
wizard only when the cluster has at least one pair of workers meeting
the bandwidth and VRAM criteria.

## Open questions

- Do we page by attention head or by token range? Head-wise is
  simpler to reason about; token-wise wastes less if the model only
  attends to recent tokens in a window.
- How do we handle a model swap on the owning worker? Probably invalidate
  all peer pages for that model uuid.
- TurboQuant is already saving 6x — is peer-VRAM even needed before
  we hit 786K on a 12GB card? Maybe the answer is "not yet" and this
  spec gets parked until a user asks for 1M+ context on consumer HW.

## Dependencies

- Per-backend paged KV cache interface. llama-cpp already has one;
  vLLM has PagedAttention natively. Backends without a paged
  representation can't participate.
- Worker-to-worker HTTP/2 wiring (currently every message goes
  through the controller). Peer-VRAM needs direct peer links.
- A scheduler watermark policy. Not in today's scheduler.

## Tracking

Originally filed as GH #149. This addendum is the design; the
implementation plan lives under a follow-up once the dependencies
are in place.
