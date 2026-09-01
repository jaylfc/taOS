# Whisplay on Pi Zero 2W as a taOS pocket interface — Spike Findings

*Spike (not a feature build). Answers derived from reading the taOS source tree
(`tinyagentos/`, `docs/`, `tests/`), not from running the Whisplay hardware. Where
the answer depends on Whisplay behaviour Jay has not yet verified, it is marked
**PENDING Jay bring-up** rather than guessed.*

---

## 1. Which existing taOS surface does it speak to?

**Maps to the Decisions app first.** The codebase already owns a
human-in-the-loop decision inbox end to end, and it already has a device-bearer
path wired into it, so a Whisplay does not need a bespoke protocol:

- `tinyagentos/decisions/decision_store.py` — the `decisions` table. A decision is
  a `single_select` / `multi_select` / `approve_deny` / `free_text` question an
  agent raises; it queues `pending` until answered, then `_route_answer_to_agent`
  in `tinyagentos/routes/decisions.py:54` posts the answer back to the asking
  agent on the A2A bus (`POST {bus}/a2a/send`, thread `decisions`) as a fallback
  to polling. `answer()` (`decision_store.py:166`) is atomic — a second answer
  409s, so the route is safe for a device that may retry.
- `tinyagentos/device_auth.py:43` — `current_user_or_device` resolves a
  `taosdev_` scoped bearer token to a **non-admin** `CurrentUser` (Invariant a:
  devices never inherit `is_admin`; Invariant c: it does not populate
  `request.state.user_id` for non-carded routes).
- `tinyagentos/auth_middleware.py:221` — `_DEVICE_BEARER_PATHS` is the exact
  allowlist a device token may pass the session gate on. It already contains the
  full read+answer arc for decisions:
  - `GET /api/decisions` (list — non-admin sees only its own user's decisions,
    `routes/decisions.py:331` → `uid = None if user.is_admin else user.user_id`)
  - `GET /api/decisions/{id}` (get — ownership-checked, `routes/decisions.py:410`)
  - `GET /api/decisions/{id}/history` (supersession lineage)
  - `POST /api/decisions/{id}/answer` (answer — with an explicit guard at
    `routes/decisions.py:494-501` that a device bearer **cannot** answer
    `execution_gate` / `delegation_gate` / `app_grant` decisions: "the phone is a
    notification surface, not an approval channel for privileged grants")

So the core pocket loop is **already reachable by a paired device today**: poll
`GET /api/decisions`, display the question + options, answer with `approve`/`deny`
or a selected value, and an agent-raised `free_text` decision can carry a short
prompt as `other_value` — which `_route_answer_to_agent` echoes back to the
agent on the bus. That single primitive (approve/reject, see what the agent is
asking, push a short prompt) is exactly what the task grades above a dashboard.

**What is missing (the gaps to close):**

1. **No device-bearer path to the notification bell.** `GET /api/notifications`
   and its read/archive siblings (`routes/notifications.py:31`) are session-only;
   their handlers use no `current_user_or_device` dependency and the paths are
   **not** in `_DEVICE_BEARER_PATHS`. The `notifications` table already has a
   `user_id` column (`notifications.py:29`), but `NotificationStore.list()`
   (`notifications.py:214`), `mark_read()`, `archive()`, and `mark_all_read()`
   do **not** accept a `user_id` filter — they return the whole table. So a device
   bearer cannot currently read the bell that is not a Decision (agent
   auth-request consent, training-complete, disk-quota, task lifecycle, etc.).
2. **No device-bearer path to the A2A bus or the taOS agent.** `POST
   /api/a2a/bus/send` and the bus read routes are agent-JWT-scoped
   (`auth_middleware.py:69-75` `_AGENT_TOKEN_PATHS` + `a2a_send`/`a2a_receive`
   scopes, verified by the route) — they require a registry identity, which a
   pocket device does not have. Likewise `POST /api/taos-agent/chat` and
   `GET /api/taos-agent/status` (`routes/taos_agent.py`) are session-only. A
   device must therefore speak to agents *indirectly*: through Decision answers
   routed back to the bus by the controller, not by posting to the bus itself.
3. **The pairing platform enum excludes Pi-class hardware.** `devices.py:31`
   (`RegisterIn.platform_supported`) and `device_pair_requests.py:46`
   (`_VALID_PLATFORMS`) whitelist only `("ios", "watchos", "android")`. A Pi
   cannot complete `POST /api/devices/pair-requests` at all today — it 400s on
   the platform before a Decision is ever raised.

## 2. What the Pi Zero 2W can actually carry

**Straight statement: it is a thin client only; no local taOS inference is
realistic.** The Pi Zero 2W (BCM2710A3, quad-core ARM Cortex-A53 @ 1.0 GHz,
**512 MB shared RAM**, no NPU, no GPU compute path) cannot host the taOS agent
runtime. The taOS agent is backed by an OpenCode host server over a LiteLLM
proxy (`routes/taos_agent.py:11`, `adapters/opencode_adapter.py`,
`taos_agent_runtime.py`); those model sizes and their token-context working set
far exceed 512 MB once the OS and a framebuffer driver are resident.

- The Whisplay **ai-chatbot** demo (`PiSugar/whisplay-ai-chatbot`) runs a tiny
  quantized model on-device, but that is a single-task chatbot, not the taOS
  agent stack (tool use, multimodal attachments, the project A2A channel, the
  task board, grants). PENDING Jay bring-up: the exact model + RAM headroom the
  Whisplay demo actually achieves on the Zero 2W, and whether it can coexist with
  a framebuffer + taOS client process.
- There is **no** taOS NPU / llama.cpp backend path that targets the Pi Zero
  2W. `tinyagentos/hardware.py` and the `sdcpp` service
  (`tinyagentos-sdcpp.service`) describe the inference surface, but those targets
  are GPU/VRAM-class cards, not a 512 MB ARM SBC. `tinyagentos/benchmark/`,
  `tinyagentos/training.py`, and `vram_reservation.py` are all VRAM-budgeted,
  which is a category error for this device.
- What the Pi **can** carry: a local framebuffer renderer for the Whisplay
  screen, button/mic input capture, and an HTTP client that polls the taOS
  controller APIs. It is the remote surface, not the compute node.

## 3. Comms path off-device

**LAN + Tailscale mesh by default; the taos.my relay is not a hard dependency.**

- The controller advertises reachable endpoints in the connection bundle
  (`docs/design/external-agent-project-invite.md:443-476`): non-loopback LAN IPv4
  (highest priority, free) then the Headscale mesh node IP (`mesh_status().node_ip`
  from `taosnet/mesh.py:130`) when joined. Clients probe `GET /api/health` in
  priority order, first 200 wins (`external-agent-project-invite.md:505-509`).
  A Whisplay on the same LAN (or tailnet) reaches the controller directly — no
  relay needed.
- The **taOSgo relay** (`taos.my`, TLS-terminating reverse proxy) is the
  off-LAN leg but is **gated behind Jay + Coolify**, per the task brief. It is
  explicitly deferred from Phase 1: `cross-user-collaboration.md:324-328`
  (Phase T0 = direct/mesh, no hub relay for the pilot) and
  `external-agent-project-invite.md:5,18` ("Phase 1 is LAN/tailnet only; the
  taos.my relay resolver is deferred"). The relay hostname is also not yet
  persisted controller-side (`external-agent-project-invite.md:467-470`: "open
  question 6").
- **Do not design a hard dependency on the relay.** The Whisplay client must
  mirror the existing external-agent transport contract: probe `endpoints` in
  order — but use the TIMED-CHECK posture only, never SSE (see §4: this client
  polls; it reuses the endpoint-priority and cursor semantics, not the stream) — and treat
  the relay as one more candidate endpoint (priority 4, only present when
  account-linked + mesh joined). The Pi being battery-powered actually *prefers*
  direct LAN/mesh over a relay hop — the relay is for genuinely off-net
  scenarios, not the local pocket case.

The relevant surfaces a Whisplay client reaches:
- Decided path (agent identity): `GET /api/a2a/bus/channels`,
  `GET /api/a2a/bus/messages?channel=…&since=…`, `POST /api/a2a/bus/send`
  (`routes/a2a_bus.py:73,118,396`). These require a registry JWT (`a2a_receive` /
  `a2a_send`), **not** a device token — so a Whisplay is not an agent on the bus.
- Agent control: `GET /api/taos-agent/status`, `POST /api/taos-agent/chat`
  (`routes/taos_agent.py:613,363`) — session-only, not device-bearer reachable.

## 4. Power and duty cycle

**Poll, do not hold a socket. A device that keeps an SSE connection open
permanently is a different product from one that wakes on push.**

- The established delivery contract (`external-agent-project-invite.md:573-628`)
  is **timed-check as the guaranteed floor, SSE as realtime-when-reachable.**
  The bundle advertises both `stream_path` (SSE) and `poll_path`
  (`GET /api/a2a/bus/messages`), a shared `cursor: ts` convention, and
  `check_interval_secs` (default 1800s). An agent SHOULD stream when it can and
  drop to the timed check when it cannot; switching modes is seamless because the
  cursor is the same.
- The Pi Zero 2W on a PiSugar battery cannot afford a persistent SSE socket —
  that pins the CPU at the radio's keep-alive cadence and kills a day's runtime
  in hours. The Whisplay should adopt the **timed-check posture**: wake on an
  interval (or on a button press), poll `GET /api/decisions` (device-bearer
  reachable today) with the last-seen decision id / cursor, render any pending
  items, and go back to sleep. The OS already models this cost:
  `tinyagentos/wake_budget.py` is the OS-enforced per-agent daily wake ceiling
  (`can_wake()`, persisted in `data_dir/wake_budget.json`), and
  `agent_heartbeat.py:40` (`HEARTBEAT_INTERVAL = 60`) is the host-side tick that
  already wakes agents to sweep their queue. A pocket device polling decisions is
  the same shape, just client-side on the Pi.
- **Wake-on-push** (the "wakes on push" end of the spectrum) already exists for
  the device class — but only APNs / UnifiedPush for ios/watchos/android
  (`notifications_push.py:388-429` `send_device_push`, branching on
  `device["platform"]` in `routes/devices.py`). A Pi has neither push endpoint,
  so **polling is the only delivery mode available to it today**; the device
  bearer has no push token registered and there is no `linux`/`embedded` platform
  handler in the push fan-out. This is not invented — it is the direct
  consequence of the platform enum in Q1, gap 3.

## 5. The camera

**Open question; do not invent a feature for it.** Jay listed it with no stated
use. Two concrete options, both contingent on his bring-up:

1. **Vision input to the taOS agent** — capture a still, POST it to
   `POST /api/taos-agent/attachments/upload` (image/sniffed to a bare uuid,
   served safe-inline from `routes/taos_agent.py:138`), then embed the resulting
   attachment URL in the `messages` body of `POST /api/taos-agent/chat`
   (`routes/taos_agent.py:456-478` already turns attachment URLs into base64
   image blocks in the last user turn). This is the minimal "show the agent what
   the camera sees" path and reuses the existing multimodal attachment seam
   end-to-end. **Auth gap: both routes are session-only today** (§1 gap 2) — a
   `taosdev_` bearer cannot call them; unavailable until a device-authenticated
   route (or controller-side bridge) exists, which is NOT in the first
   increment. PENDING Jay bring-up: whether the camera is a still sensor or a
   stream, and its resolution/MJPEG pipeline on the Zero 2W.
2. **Image capture as a Decision trigger** — a motion or button event raises a
   `free_text` (or `approve_deny`) Decision to the owner. **Auth gap: decision
   CREATION (`POST /api/decisions`) is session-only** — the device-bearer
   allowlist covers list/get/history/ANSWER only, so this too needs a
   device-authenticated route or bridge before it works; attach the captured
   frame as notification `data`. This turns the Pi into an event push device
   rather than a vision-input device.

No taOS-wide "camera as a sensor" abstraction exists; the only capture point is
the attachment upload in the taOS agent route. If Jay wants a reusable camera
primitive across agents, that would be a separate, larger effort — out of scope
for this spike.

---

## Recommended first build increment (one card)

**Device-bearer notification read path for the pocket device.**

The decision path is ALREADY device-bearer reachable (see Q1). The next card
closes the bell gap so the device has a complete inbox rather than only pending
decisions:

- Add `GET /api/notifications`, `GET /api/notifications/count`,
  `POST /api/notifications/{id}/read`, `POST /api/notifications/{id}/archive`, and
  `POST /api/notifications/read-all` to `_DEVICE_BEARER_PATHS` in
  `tinyagentos/auth_middleware.py:221`.
- Switch those handlers to `current_user_or_device`
  (`tinyagentos/device_auth.py:43`) so a device bearer resolves to its owner's
  non-admin identity — mirroring exactly how the Decisions routes already behave.
- Add a `user_id` filter to `NotificationStore.list()` / `mark_read()` /
  `archive()` / `mark_all_read()` (`tinyagentos/notifications.py`). The column
  already exists (`notifications.py:29`) and is already indexed
  (`notifications.py:147`); the store just never filters on it. Scope reads and
  writes per owner so a device never sees another user's bell.
- As a one-line prerequisite folded into the same card: add `"linux"` to
  `_VALID_PLATFORMS` (`routes/device_pair_requests.py:46`) and the
  `RegisterIn` platform whitelist (`routes/devices.py:31`) so the Pi can actually
  pair and obtain a `taosdev_` token to authenticate the above.

This is bounded (auth middleware + one store + route deps + the platform string),
fully testable against the existing `tests/test_routes_notifications.py` and
`tests/test_device_store.py` patterns, and reuses the device-bearer precedent
already set by Decisions. It does **not** add a new protocol, a new push
delivery mechanism, or any local inference — the Pi polls.

Follow-on cards (out of this increment): a device-bearer path to the A2A bus is
**not** recommended — the bus is agent-identity-scoped by design; the device
should stay a human surface that raises/answers Decisions, which the controller
routes to agents on the bus. Off-box push delivery for a Pi-class device is
gated on the relay work and the platform enum; defer until Jay's bring-up
confirms the battery/poll reality.

---

## Acceptance

- Findings doc committed under `docs/design/` (this file).
- Each claim about the taOS codebase cites a source path in the repo (no
  README-only assertions). Hardware figures (Pi Zero 2W CPU/RAM, battery, radio
  cost) are vendor-spec-derived, not repo-cited, and battery behaviour is
  PENDING Jay bring-up.
- Hardware-dependent claims (Whisplay ai-chatbot model + RAM headroom, camera
  sensor type) are marked PENDING Jay bring-up.
- No taOS code changes in this card (findings + first-increment recommendation
  only). The recommended first increment is a pointer for the next card.
- The recommendation does not design a hard dependency on the taos.my relay;
  the relay deferral is flagged against its gate (Jay + Coolify, Phase 1).
