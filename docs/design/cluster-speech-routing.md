# Design: speech (ASR and TTS) routed to paired worker nodes

**Status:** Revision 2, for review. Jay decided (via taOS-dev, 2026-10-06) to **allow remote ASR
and TTS through the worker agent**, routed to paired workers, over the LAN and over taOSgo. This
revision applies the lead review (#3572 comment 6024979159) and Jay's answers (comment
6025016170); section 11 lists what changed.
**Date:** 2026-10-06
**Evidence:** omp-strata-lab experiment 023 (offline speech across the cluster) and its working
server, `tools/speech/speechd.py`.

## 1. What exists today

**Gateway speech routes** (`tinyagentos/llm_gateway/router.py`):

- `audio_transcriptions` (`POST /api/llm/v1/audio/transcriptions`) reads a capped multipart body,
  loads `stt.load_manifest(data_dir)` (`<data_dir>/voice/stt/manifest.json`: `port`, `model`),
  checks `caller.may_use`, accepts only `taos-stt-default` or the manifest's `model`, and calls
  `stt.transcribe_pcm(pcm, manifest)`.
- `audio_speech` (`POST /api/llm/v1/audio/speech`) does the same with `tts.load_manifest`
  (`port`, `model`, `sample_rate`), `tts.check_sample_rate` and `tts.open_speech(text, manifest)`,
  streaming through `tts.SpeechResponse`.
- Both modules state hard rules "each pinned by a test": **the host is the literal `127.0.0.1`**
  (`_LOOPBACK`; no `host` or `url` manifest key is ever read), **no fallback of any kind** (503
  when the daemon is down), `trust_env=False` so a proxy variable cannot carry audio off the
  host, and no logging of audio, text or transcripts.
- The pins are `test_the_daemon_is_always_reached_on_loopback_whatever_the_manifest_says` in
  `tests/test_llm_gateway_stt.py` and `tests/test_llm_gateway_tts.py`, plus
  `test_manifest_host_and_url_keys_are_ignored` in the tts tests.

**Worker agent** (`tinyagentos/worker/agent.py`, `worker_manifest.py`):

- `worker_manifest.load_manifest` reads `worker-models.json`, whose documented `software` values
  are `llamacpp|embed|kokoro|whisper` and `capability` values `text|embed|tts|asr`.
- `SOFTWARE_TO_BACKEND_TYPE` maps only `llamacpp` and `embed` (both to `llama-cpp`), and
  `WorkerAgent.detect_backends` drops any other entry: `continue  # unknown software type,
  silently skip`. So **every `kokoro` and `whisper` entry is discarded**.
- Probing is limited to `_PROBEABLE_TYPES` (`rkllama`, `ollama`, `hailo-ollama`, `llama-cpp`,
  `llama-swap`, `vllm`, `exo`, `mlx`, `sd-cpp`). Capabilities come from
  `scheduler/backend_catalog.BACKEND_CAPABILITIES`, which has no speech capability.
- `detect_capabilities` unions backend capabilities; the controller selects workers with
  `ClusterManager.get_workers_for_capability` (online, not draining, `kind != "device"`).
- The stock worker agent runs **no HTTP server**. `_advertised_url` returns `advertise_url`,
  else `TAOS_ADVERTISE_IP` (+ `worker_port`), else the first live loopback backend URL. Only the
  browser worker (`worker/browser_main.py`, `browser_server.create_browser_worker_app`) serves
  an API, with a bearer token.
- `hardware._detect_ram` reads only `/proc/meminfo`, so a Mac reports `ram_mb: 0`, although
  `_detect_gpu` already calls `sysctl -n hw.memsize` on Apple Silicon.

**Controller to worker traffic.** `WorkerInfo` (`cluster/worker_protocol.py`) says each backend
`url` is worker-local and "cross-host callers must route through the worker agent (worker.url),
never dial backend url directly". The only cross-host dispatch today is
`cluster/router.TaskRouter.route_request` (JSON only, admin-only `POST /api/cluster/route`).
The gateway's chat and embeddings tables are built from `config.backends` only (`resolve.routing_table`,
`embeddings._backends`), so **this spec adds the first gateway route to a worker**. Worker-reported
backends appear in `/api/providers` (`routes/providers.py`, `aggregate_catalog`) but are not routed.

**Pairing.** `cluster/pairing_store.ClusterPairingStore` mints one 32-byte `signing_key` per
worker; `cluster/worker_auth.require_worker_hmac` verifies worker-to-controller requests signed
over `f"{timestamp}.{METHOD}.{path}.{sha256(body)}"` with a 300 s skew window. The controller
holds the same key (`get_signing_key(name)`), so it can sign requests to the worker.

**taOSgo.** `tinyagentos/taosnet/mesh.py` joins the host to the account's Headscale mesh with
system tailscale (`mesh_up`: `tailscale up --login-server <login_server()> --authkey ...`;
`login_server()` defaults to `https://hs.taos.my`, `TAOS_HEADSCALE_URL` overrides it), and
`mesh_status()` reads `tailscale status --json` (joined, tailnet, `node_ip`, and peers tagged
`tag:guest`). Traffic between mesh nodes is WireGuard, direct when possible and through the
mesh's relays otherwise. `routes/taosgo.app_join` mints the preauth key, but its body is still a
placeholder ("In production, this would call the Headscale admin API"), and the worker agent has
no mesh join of its own; the browser worker only reads its tailnet IP
(`browser_container._detect_tailscale_ip`, `tailscale ip -4`).

**Worker URL trust.** `routes/cluster.register_worker` takes `body.url` from the worker's signed
registration and does not compare it with the source address. The one existing cross-check is
in the incus enrolment route, which refuses an `incus_url` whose host differs from the
registered `worker.url` host.

**Lab evidence (023).** speechd serves the taOS daemon contracts (`POST /stt`, `POST /tts`) plus
OpenAI routes, `/health` and `/v1/models`, bound to 127.0.0.1, on a Mac mini M4 (Parakeet TDT
0.6B v3 MLX, Kokoro-82M MLX, Piper cori-high) and a CPU-only i5 (Parakeet v2 int8 via
sherpa-onnx, Piper). `/stt` and `/tts` worked end to end on both (WER 0 on the 5 s clip,
`X-Sample-Rate: 22050`, `X-Channels: 1`). A dry run of the stock worker agent at `4fc0f619`
(`tools/speech/worker_dryrun.py`, never registering) mapped **0 of 3** declared speech models on
both nodes, advertised no `asr`/`tts`, missed LM Studio on :1234, and reported `ram_mb: 0` on the
Mac. Mac M4 numbers: STT 0.084 s for a 5 s clip, 2.76% WER; Kokoro first audio 0.25 s, RTF 0.077.

## 2. Goals and non-goals

Goals:

1. `/audio/transcriptions` and `/audio/speech` can target a paired worker's `asr` / `tts`
   capability, through the worker agent, chosen explicitly, over two transports: the **LAN**
   and **taOSgo** (the account mesh, the path taOS promotes for off-LAN workers).
2. Speech entries in `worker-models.json` reach the controller (new backend type and mappings,
   a `piper` software value).
3. A catalog variant that runs Parakeet without PyTorch (sherpa-onnx int8).
4. Worker-agent portability found by the lab: macOS RAM; optionally LM Studio and a dry run.

Non-goals and kept rules:

- **No fallback, still.** One request has exactly one target. Local never falls over to a
  worker or the reverse, and one worker never to another. A down target is a 503.
- **Local is unchanged.** With no worker route configured, both routes behave byte for byte as
  today, and a manifest still cannot redirect the gateway (no `host`/`url` key is read).
- No cloud speech engines. taOSgo's relays only carry WireGuard-encrypted traffic between the
  account's own nodes. No audio, text or transcript is logged, traced or kept, on either side.
- The 7838 agent listener allowlist (`listener.GATEWAY_PATHS`) is unchanged; whether agents get
  audio paths there is a separate decision.
- No speech-model download or install orchestration on workers (operators run the daemon; the
  catalog entry below is for installers).

## 3. Worker side

### 3.1 Manifest and backend type

- `worker-models.json` `software` gains `piper` and a generic `speechd` (any server speaking the
  taOS daemon contracts). `kokoro` and `whisper` keep their meaning. Optional per-entry field:
  `sample_rate` (required for a `tts` entry to be routable, see 4.3).
- `SOFTWARE_TO_BACKEND_TYPE` maps `kokoro`, `whisper`, `piper` and `speechd` to a new backend
  type **`speechd`**. Unknown values are still skipped, but with `_warn_probe_entry_once`
  instead of silently, so the next manifest typo is visible.
- `speechd` joins `_PROBEABLE_TYPES` and `BACKEND_CAPABILITIES` (`{"asr", "tts"}`), and is
  **not** a default probe candidate: it is only probed where a manifest entry declares it.
- `_probe_models` for `speechd`: `GET /health` must answer 200 (alive); then `GET /v1/models`
  if served (speechd lists `kind: stt|tts` and `sample_rate`), else the manifest's names.
- **Capabilities per entry, not per type.** A `speechd` backend advertises only the
  capabilities its declared entries carry (`asr` and/or `tts`), so a TTS-only node never
  advertises `asr`. This needs a change in `detect_backends`, which today fills `capabilities`
  from `BACKEND_CAPABILITIES.get(backend_type)` both for a probed backend and for the synthetic
  `stopped` entry; `detect_capabilities` already prefers each backend's own `capabilities`.
- Several entries on one port (023 runs STT and two voices in one process) attach to one
  backend, which the existing `_candidate_key` grouping already does.

Resulting registration for the Mac (023 manifest with `software` changed and the new `sample_rate` field added):

```json
{"name": "speechd:8771", "type": "speechd", "url": "http://localhost:8771",
 "capabilities": ["asr", "tts"], "status": "ok",
 "available_models": [
   {"model_id": "parakeet-tdt-0.6b-v3", "capability": "asr", "software": "speechd", "port": 8771, "status": "loaded"},
   {"model_id": "kokoro-82m-bf16", "capability": "tts", "software": "speechd", "port": 8771, "sample_rate": 24000, "status": "loaded"},
   {"model_id": "piper-en_GB-cori-high", "capability": "tts", "software": "piper", "port": 8771, "sample_rate": 22050, "status": "loaded"}]}
```

### 3.2 Speech relay on the worker

The gateway must not dial `localhost:8771` (that is the worker's loopback), so the worker agent
gains a small relay, modelled on the browser worker's API:

- `tinyagentos/worker/speech_relay.py`: an ASGI app with `POST /worker/speech/stt` and
  `POST /worker/speech/tts`, served by the worker agent (uvicorn, as `browser_main.serve` does)
  only when at least one `speechd` entry is declared and `TAOS_WORKER_SPEECH_RELAY=1`.
- It binds the advertised address and port: the LAN address (`TAOS_ADVERTISE_IP`), or on
  taOSgo the worker's tailnet IP (4.4). A relay without an explicit advertised address refuses
  to start, because `_advertised_url` would otherwise fall back to a loopback backend URL, and it
  never binds `0.0.0.0` or loopback.
- **Port: 7841, a new default.** `WorkerAgent.worker_port` defaults to 0 today and the browser
  worker uses 7080. 7841 is not used anywhere in `tinyagentos/` on `dev`, and clears the core
  and bundled ports found there (6969 web, 6970 browser proxy, 7832 qmd, 7833 rkllama, 7834
  LiteLLM legacy, 7836 hailo-ollama, 7837 MLX, 7838 agent listener, 7864 sd-cpp). The PR that
  adds it re-checks that list and makes it overridable (`TAOS_WORKER_SPEECH_PORT`).
- Request: the target `model_id` in a header (`X-TAOS-Speech-Model`), body exactly the daemon
  contract (raw PCM16 for `/stt`, `{"text"}` for `/tts`). The relay looks the model up in the
  **current manifest**, and forwards only to `http://127.0.0.1:<that entry's port>/stt|/tts`.
  It never takes a host, port or path from the request (no SSRF through the relay).
- Responses stream back unchanged (chunked PCM with `X-Sample-Rate` and `X-Channels`), and
  daemon status codes pass through (503 busy, 4xx rejection). `trust_env=False`, no logging of
  bodies, the same 960,000-byte cap as `stt.MAX_PCM_BYTES` and the gateway's 4,096-character
  `tts.MAX_INPUT_CHARS`, enforced before forwarding.
- **Auth: controller-signed HMAC.** Each request carries `X-TAOS-Controller-Timestamp` and
  `X-TAOS-Controller-Signature`, an HMAC-SHA256 with the pairing `signing_key` over
  `f"c2w.{worker_name}.{timestamp}.{METHOD}.{path}.{model_id}.{sha256(body)}"`. The `c2w.`
  prefix and the worker name separate it from worker-to-controller signatures, so neither can
  be replayed as the other. Skew window 60 s (shorter than `worker_auth`'s 300 s, since this
  carries user audio); the worker keeps a bounded cache of seen signatures for that window to
  refuse replays. An unpaired worker (no `signing_key`) never starts the relay.

## 4. Controller side

### 4.1 The rule: local or worker, chosen explicitly

The docstring rule "the host is the literal `127.0.0.1`" becomes:

> The daemon is reached either on the literal `127.0.0.1` at the local manifest's port
> (**local target**), or through a paired worker's speech relay at the URL that worker
> registered (**worker target**). The host never comes from a manifest, a request body, a
> header or a model name. A request resolves to exactly one target; there is no fallback.

Resolution in `stt.resolve_target(state, requested)` / `tts.resolve_target(...)`:

1. `taos-stt-default` / `taos-tts-default`: the configured default (`voice.stt.default` /
   `voice.tts.default` in `config.yaml`): `local` (the default, today's behaviour) or
   `<worker>/<model_id>`.
2. The local manifest's `model`: local.
3. `<worker>/<model_id>`: a worker target, if eligible (4.2).
4. Anything else: 404 `model_not_found`, as today.

The caller must `may_use` the name it sent, as today. Choosing a worker model by its qualified
name is what makes the route explicit and keeps "no fallback" literal.

### 4.2 Worker eligibility

A worker target is accepted only when all hold, else a 404 (unknown) or 503 (known, unusable).
The checks live in one helper, `eligible_worker_url`, in a new `llm_gateway/speech_targets.py`
that `stt.resolve_target` and `tts.resolve_target` share:

- `voice.remote_workers` is on (default **off**; an admin turns it on);
- the worker is in `ClusterManager` with a pairing `signing_key`, `kind == "worker"`, status
  `online` (not draining or updating), and it advertises the capability (`asr` or `tts`);
- `model_id` is in that worker's `available_models` with the matching capability; for `tts`, it
  declares an integer `sample_rate`;
- `worker.url` parses as `http` with a literal IP and an explicit port, and the IP is in an
  **allowed transport range**:
  - **LAN:** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7` (the `_LAN_NETS` of
    `worker/agent.py`);
  - **mesh:** `100.64.0.0/10` or `fd7a:115c:a1e0::/48`, only when the mesh check in 4.4 passes.
- **Never loopback, never the controller itself.** `localhost`, `127.0.0.0/8`, `::1`,
  IPv4-mapped loopback, link-local, any address bound to the controller's own interfaces
  (its LAN and tailnet IPs), and every public address are refused. This is deliberately
  **not** `_normalize_probe_url`: that function validates a worker's own backend URLs, where
  loopback is the point. On the controller, a worker registering `http://127.0.0.1:<port>`
  would otherwise aim signed audio at the controller's own loopback services.
- **Pinned to the registration source.** `register_worker` records the connection's source
  address (`request.client.host`) beside `worker.url`. A worker is eligible only when the
  `worker.url` host equals that source address, the same idea as the incus enrolment check.
  A worker behind NAT or a proxy fails this and is refused with a reason; it is not guessed.

### 4.3 Transport

- `stt.transcribe_pcm(pcm, target)` and `tts.open_speech(text, target)` take a `SpeechTarget`
  (`Local(port, model)` or `Worker(name, base_url, model, key)`) instead of the manifest dict.
  `Local` builds exactly today's URL; `Worker` posts to `<base_url>/worker/speech/stt|tts` with
  the signed headers, `trust_env=False`, connect timeout 2 s (LAN), read 60 s as today.
- Error mapping is unchanged: unreachable or 503 is `stt_unavailable` / `tts_unavailable`, 4xx
  is `invalid_audio` / `invalid_request`, anything else 502. A relay 401 (bad signature or
  skew) is a 502 to the caller with a fixed message, and a controller log line naming the
  worker (never the payload).
- TTS keeps "we do not guess": the relay's `X-Sample-Rate` must equal the worker entry's
  `sample_rate`, `X-Channels` must be `1`, and `check_sample_rate` and `PcmResampler` apply
  unchanged (native or 16 kHz).
- STT keeps `wav_to_pcm` and the 30 s cap on the controller, so a worker never sees a malformed
  or oversized upload.
- `stt.py` and `tts.py` still import nothing from chat routing; they gain a dependency on the
  cluster manager and pairing store through `app.state`, passed in by the route.

### 4.4 taOSgo transport

A worker reached over taOSgo uses the same relay, the same signing and the same rules; only the
addressing and the eligibility check differ.

- **Joining.** The worker joins the account mesh with the same mechanism the host uses:
  `taosnet.mesh.mesh_up` (system tailscale, `login_server()`), given a single-use preauth key
  that an admin mints for that worker on the controller. Today `/api/taosgo/app-join` returns a
  placeholder key, so real Headscale key issuance is a prerequisite (rollout step 4).
- **Addressing.** The relay binds the worker's tailnet IP (`tailscale ip -4`, as
  `browser_container._detect_tailscale_ip` reads it), and the worker registers
  `http://<tailnet IP>:7841`. The worker reaches the controller over the mesh too, so the
  registration's source address is the same tailnet IP and the pin in 4.2 holds.
- **Eligibility.** A mesh-range URL is accepted only if the controller is itself joined, and its
  own `tailscale status --json` lists that IP as an **online peer** that is **not** tagged
  `tag:guest` (guest instances from cross-user preauth never receive speech). This needs a
  `mesh_peers()` beside `mesh_status()` returning every peer's IPs, online state and tags;
  `mesh_status()` keeps only guests today.
- **Labels.** When the controller's mesh is the taOSgo one (its control server is
  `login_server()`), the transport is reported as `taosgo`. A mesh-range peer on any other
  tailnet (a user's own Tailscale) is accepted under the same peer check and reported as
  `manual-vpn`: allowed because the user set it up, but docs and UI never suggest it; taOSgo is
  the path they point to.
- **Signing and limits.** The same `c2w.` HMAC, 60 s window and replay cache. WireGuard adds
  encryption on this path; the HMAC is still required. Timeouts: connect 5 s on the mesh
  (relayed paths are slower to open), read 60 s. Added latency is measured in the lab before
  rollout.

## 5. Catalog

- **`piper` software value** (3.1), matching the existing `app-catalog/services/piper`.
- **Parakeet without PyTorch.** `app-catalog/models/parakeet-tdt-0.6b/manifest.yaml` has one
  variant, `nemo` (600 MB `.nemo`, backends `nemo` / `transformers`). Add:

```yaml
- id: sherpa-onnx-int8
  name: sherpa-onnx int8 (640MB, CPU, no PyTorch)
  format: onnx
  size_mb: 661
  min_ram_mb: 1536          # ~1.2 GB RSS reported (sherpa-onnx #2626); 023 omarchy RSS 2.3 GB incl. Piper and Kokoro
  download_url: https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2
  files:                    # sha256 of the extracted files, from the lab copy used in 023
    encoder.int8.onnx: a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab
    decoder.int8.onnx: b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e
    joiner.int8.onnx:  7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2
    tokens.txt:        ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d
  requires:
    backends:
    - id: sherpa-onnx
      targets: [cpu]
      min_ram_mb: 1536
```

  and `hardware_tiers` recommending it for `arm-cpu-8gb` and `cpu-only`. A `sherpa-onnx` backend
  id (pip `sherpa-onnx`, the 023 version is 1.13.8) is needed in the backend list.
- **Archive support in `download_installer` (decided).** Today a variant carries one
  `download_url` and one `sha256`, and `download_file` checks that hash on the downloaded file;
  there is no extraction. A variant with `files:` is an archive: download (through the existing
  `_ssrf_guard` and pinning), extract `.tar.bz2`, `.tar.gz` or `.zip` into a temporary directory
  under the target, refuse any member that is absolute, contains `..`, is a link or a device,
  or is not listed in `files:`, check each listed file's sha256 (all must be present), then move
  the directory into place atomically. An optional archive-level `sha256` is checked first when
  given.
- Measured on the i5 CPU under contention (023): 0.70 s for a 5 s clip, RTF 0.11 to 0.125,
  3.26% WER on the 20-utterance set.

## 6. Worker-agent portability

- **macOS RAM (required).** `hardware._detect_ram`: when `/proc/meminfo` is missing and
  `platform.system() == "Darwin"`, use `sysctl -n hw.memsize` (bytes to MiB), the call
  `_detect_gpu` already makes. Test: a Darwin monkeypatch yields 24576 for 25769803776 bytes.
- **LM Studio (optional).** A probe type `lmstudio` in `_PROBEABLE_TYPES` and
  `_OPENAI_MODELS_TYPES` (OpenAI `/v1/models`), capabilities `{llm-chat, embedding}`, with
  `("lmstudio", "http://localhost:1234")` as a default candidate. Port 1234 is not exclusive to
  LM Studio, so the probe confirms it with a second request to LM Studio's own REST API before
  labelling it (to verify against LM Studio's docs in the PR). Until then the documented
  workaround is `TAOS_EXTRA_BACKENDS=llama-cpp=http://127.0.0.1:1234`.
- **Dry run (optional).** `python -m tinyagentos.worker --dry-run` (controller argument optional
  with it): runs `detect_hardware`, `detect_backends` and `detect_capabilities`, prints the exact
  registration payload as JSON plus a "manifest entries declared vs mapped" summary, and exits 0
  without `register()` or `heartbeat()`. This is what 023's `worker_dryrun.py` had to do by hand.

## 7. Security and privacy

- Remote speech is **off by default** (`voice.remote_workers`); an admin turns it on.
- Hosts come only from the cluster registry of paired, online workers whose URL is a LAN or
  verified mesh-peer IP that matches the registration source, never loopback or the controller
  itself; never from a manifest, request or model name. The relay forwards only to loopback
  ports its own manifest declares.
- **Trust assumption, stated:** a paired worker is trusted with the audio and text routed to
  it. `worker.url` is self-reported in its signed registration, so a compromised paired worker
  could still receive what is routed to it; the source pin and the range rules stop it from
  aiming that traffic at the controller or at a third host.
- Every relay request is HMAC-signed with domain separation, a 60 s window and replay refusal;
  the worker refuses unsigned or unpaired traffic.
- **Confidentiality:** accepted by Jay. On the LAN the relay is plain HTTP with HMAC integrity,
  like the browser worker's API (audio is readable on the LAN). On taOSgo it is inside
  WireGuard.
- No logging or tracing of audio, text or transcripts on the controller, the relay or speechd
  (023's speechd keeps its access log off). Errors log fixed strings and the worker name.
- `kind == "device"` nodes are never targets, consistent with `get_workers_for_capability`.

## 8. Config and flags

- Controller `config.yaml`: `voice.remote_workers` (bool, default false),
  `voice.stt.default` and `voice.tts.default` (`local` or `<worker>/<model_id>`, default
  `local`). Only an admin sets these; a worker default is resolved per request with the same
  eligibility checks, so an ineligible default is a 503, never a silent switch to local.
- Worker: `TAOS_WORKER_SPEECH_RELAY=1`, `TAOS_ADVERTISE_IP` (LAN) or the tailnet IP (taOSgo),
  and `TAOS_WORKER_SPEECH_PORT` (default 7841, new).
- No change to `TAOS_STT_MANIFEST` / `TAOS_TTS_MANIFEST`.

## 9. Tests

Replaced (the old rule no longer holds as written):

- `test_the_daemon_is_always_reached_on_loopback_whatever_the_manifest_says` (stt and tts)
  becomes `test_local_target_is_loopback_and_worker_target_is_the_registered_url`: a manifest
  with `host`/`url` keys still yields `http://127.0.0.1:<port>/stt`, and a `<worker>/<model>`
  name yields `<worker.url>/worker/speech/stt`, nothing else. `test_manifest_host_and_url_keys_are_ignored`
  stays as is.

New, controller (`tests/test_llm_gateway_stt.py`, `test_llm_gateway_tts.py`, new
`tests/test_llm_gateway_speech_routing.py`):

- remote off: a `<worker>/<model>` name is a 404 and no request leaves the host;
- refusals: unpaired worker, `kind == "device"`, offline or draining, no `asr`/`tts`
  capability, model not in `available_models`, wrong capability, tts entry without
  `sample_rate`, `worker.url` with a hostname, a public IP or link-local: each refused, and
  the fake relay receives nothing;
- **loopback refusal (the review's must-fix):** a paired, online worker registered as
  `http://127.0.0.1:<port>`, `http://localhost:<port>`, `http://[::1]:<port>` or
  `http://[::ffff:127.0.0.1]:<port>`, and one registered with the controller's own LAN IP, is
  refused and nothing is sent to the controller's loopback. Declared with
  `@pytest.mark.guards("tinyagentos.llm_gateway.speech_targets:eligible_worker_url",
  replace=[(<the controller-side range check>, <_normalize_probe_url(url)>)])`: swapping in the
  worker-side check, which accepts loopback, must make it fail;
- source pin: a `worker.url` host that differs from the recorded registration source is refused;
- taOSgo: a mesh-range URL is refused when the controller is not joined, when the IP is not a
  peer, when the peer is offline, and when it is tagged `tag:guest`; it is accepted as `taosgo`
  or `manual-vpn` according to the controller's control server;
- **no fallback:** worker down gives 503 and the local daemon receives nothing; local down
  gives 503 and no worker receives anything;
- signing: the relay receives the `c2w.` signature over the exact body; a tampered body fails;
- TTS rate check against the worker entry's `sample_rate`; 16 kHz resampling still works;
- request fields named `host`, `url` or `port` are ignored everywhere.

Each guard test declares `@pytest.mark.guards(...)` with a `replace` pair that reproduces the
real defect (for example, reading the host from the manifest, or falling back to local).

New, worker (`tests/test_worker_manifest.py`, new `tests/test_worker_speech_relay.py`):

- `kokoro`, `whisper`, `piper`, `speechd` entries map to `speechd` and reach the payload with
  per-entry capabilities (023's two manifests as fixtures: 3 of 3 mapped);
- unknown software logs a warning once;
- relay: unsigned, stale, replayed or wrong-worker signatures refused; an undeclared model is a
  404; the forward target is always `127.0.0.1:<declared port>`; caps enforced; streaming
  passes through; no relay without an advertised address or a signing key;
- `_detect_ram` on Darwin; `--dry-run` never calls `register` or `heartbeat`.

## 10. Rollout

Small PRs against `dev`, each with its changelog fragment and the doc gate satisfied:

1. Worker manifest: `speechd` type, `piper` and `speechd` software values, per-entry
   capabilities, warning on unknown software; macOS RAM fix. (No controller behaviour change.)
2. Worker speech relay with controller-signed HMAC, behind `TAOS_WORKER_SPEECH_RELAY`; the
   registration source pin in `register_worker`.
3. Gateway: `SpeechTarget`, resolution and LAN eligibility, the replaced and new tests, behind
   `voice.remote_workers` (default off).
4. taOSgo: real preauth issuance in `app_join`, worker mesh join, `mesh_peers()`, and the mesh
   eligibility path.
5. Catalog: archive support in `download_installer` and the sherpa-onnx int8 variant.
6. Optional: LM Studio probe; `--dry-run`.

Validation in the lab before step 3 is enabled anywhere: the Mac and omarchy as real paired
workers with 023's manifests, STT and TTS through the controller, and the same WER and
first-audio numbers as direct calls, plus the added LAN latency.

## 11. Decisions (revision 2)

| Item | Decision | Where |
|---|---|---|
| Review 1 (must-fix) | Loopback, `localhost` and the controller's own addresses are never eligible; refusal test with a guards pair | 4.2, 9 |
| Review 2 | `worker.url` pinned to the registration source; trust assumption stated | 4.2, 7 |
| Review 3 | 7841 named as a new default, checked against core ports, overridable | 3.2, 8 |
| Q1 transport | HMAC over plain HTTP accepted on the LAN; taOSgo added as a transport | 4.4, 7 |
| Q2 Tailscale | `100.64.0.0/10` accepted as a verified mesh peer (`manual-vpn`); never promoted; taOSgo is the promoted path | 4.2, 4.4 |
| Q3 default alias | `voice.*.default` may name `<worker>/<model_id>`, admin-configured | 4.1, 8 |
| Q4 catalog | `download_installer` gains archives with per-file sha256 (`files:`) | 5 |
| Q5 software values | Only `piper` and `speechd` are added | 3.1 |

No questions are open. The remaining unknowns are measurements for the lab: LAN and taOSgo
latency added to STT and first TTS audio, and relayed-path behaviour when no direct mesh path
exists.
