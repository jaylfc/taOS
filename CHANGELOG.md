# Changelog

All notable changes to taOS are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow semver beta: `1.0.0-beta.N`, bumped on each dev->master promotion.

## [Unreleased]

## [1.0.0-beta.52] - 2026-09-08

### Added

- `bot-review-gate`: the head-SHA reconciler now fails closed when it cannot
  update a stale check run. Previously a failed PATCH (network error or a
  non-2xx refusal) was treated as a no-op, so the reconciler published a fresh
  passing run alongside the stale FAILURE it had not managed to update. Because
  `mergeStateStatus` keys off ANY failing run, that left the PR pinned on
  UNSTABLE while the reconcile reported a successful write.
- `bot-review-gate`: the workflow's `--head-sha` wiring and the multi-page
  check-run aggregation are now covered by tests; both were previously
  unasserted, so either could regress with the suite fully green.
- New cross-project kanban aggregate read endpoint (`GET /api/projects/tasks/aggregate`) lets an authorized agent or session owner read tasks across all active projects they can access in a single call.
- Assistant Studio now asks before changing your assigned personal assistant.
  Notes, tasks, calendar and deliverables are all scoped to the assigned PA, so
  an accidental pick in the dropdown used to re-scope the whole workspace with
  no way to notice. First-time assignment is unaffected (#2569).
- Fixed: on roughly square displays (such as the Unihertz Titan 2) the mobile dock
  sat well above the bottom edge. The fixed 54px browser-chrome reserve is now
  reduced to the standard 12px gap on square viewports, where the `100dvh` root
  already accounts for browser chrome. Tall phones are unchanged.
- Sign in with a PIN on a touchscreen. A taOS device with no keyboard could not be signed into from its own screen: the kiosk came up fullscreen and asked for a typed password. taOS now renders its own on-screen keyboard on `/auth/login` and `/auth/setup` (full QWERTY, symbol layers, and a numeric keypad chosen by the field), and offers a {4,12}-digit PIN as a second sign-in method. A PIN is accepted **only from the device's own screen** — never over the LAN and never through a proxy — so it stays a convenience for the panel in front of you rather than a weaker password for the network. Off-console the login page does not mention that a PIN exists at all (#tsk-2qaisb).
- Choose a sign-in method at install and in Settings. The first-run wizard offers an optional PIN alongside the password when it is being run at the machine's own screen, and Settings → Account gains a PIN card that sets, changes or turns one off. Setting a PIN costs the account password even from an existing session — a PIN is a lasting way back into the device — while turning one off costs nothing, because demanding a typed password to disable it would be unperformable on the very device the feature serves (#tsk-2qaisb).
- Failed PIN attempts back off on an escalating delay (30s, 5m, 15m) and never lock permanently. A terminal lockout would re-create the keyboard-less brick this feature exists to fix (#tsk-2qaisb).
- Password reset foundation: a server-side `PasswordResetStore` (`tinyagentos/password_reset_store.py`) that mints cryptographically-random reset tokens, persists only their SHA-256 hash (never the plaintext), enforces a 30-minute TTL, consumes tokens with one atomic `UPDATE ... WHERE used=0` to defeat double-spend races, invalidates a user's prior outstanding tokens when a new one is minted, and is wired on `app.state.password_reset` via the real app factory.
- Hailo-10H .hef model catalog manifests for qwen2.5-1.5b, qwen3, qwen2.5-coder-1.5b, qwen2-1.5b, llama-3.2-1b, and deepseek-r1-1.5b, all targeting the hailo-ollama backend via `hailo-ollama pull` with `hef_h10h` content hashes instead of direct download URLs.
- Board UI for quarantined tasks: visible Quarantined column/section with strike count and latest strike reason, plus a lead-only Unquarantine action that calls the backend route and surfaces the 409 state cleanly. Quarantine transitions now appear without manual reload via the board's live event stream.
- **bot-review-allow override label for bot-review-gate**: `scripts/check_bot_review.py` now reads the PR's labels from the GitHub API at run time and, when the lead-applied `bot-review-allow` label is present and the only CodeRabbit output is a rate-limit stub or auto-generated scaffolding, waives the FAIL verdict to exit 0 with an explicit WAIVED message (never a genuine PASS). The waiver covers only the stub verdict class (EXIT_STUB), not a cannot-fetch infrastructure error (EXIT_ERROR), so fail-closed is preserved on true cannot-see states. The `bot-review-gate` workflow now re-runs on `pull_request` `labeled` and `unlabeled` activities so applying and removing the label both re-run the gate, making the waiver revokable in practice. This is the escape hatch that unblocks requiring `bot-review-gate` as a required status check on `dev`.
- Library app: heavy-tier media download (yt-dlp) for YouTube URLs with quality preferences (360/480/720/1080/best), per-source auto-download rules with fnmatch matching, and storage accounting (size, kind breakdown).
- Add regression test for bound project channel presence in standalone mode (tsk-564mgr)
- `GpuArbiter.queue_snapshot()` now includes `resource_id` per entry, and a new `cancel_queued_for_resource(resource_id)` cancels every queued GPU op targeting a fenced resource through the same race-safe `_cancelled_ids` path as `cancel_op`. Fence handlers can now proactively cancel queued ops instead of waiting for `claim_lease` to reject them on admission (#tsk-5aiafr).
- Agent container provisioning executor (P2): `POST /api/containers/requests/{id}/provision` creates an incus/LXC container for an approved request, binds it to the requesting agent + project via environment variables, and records the container name on the request. `POST /api/containers/requests/{id}/destroy` deletes the request and, if already provisioned, the underlying container, returning quota to the agent. `GET /api/agents/containers/quota` returns the calling agent's quota, threshold, active count, and remaining slots. `POST /api/container-requests` is an alias for the existing create endpoint.
- Quota accounting is now atomic: count+create is serialized by an asyncio lock so that a quota=1 race cannot double-approve. The `rejected` state is added as a terminal state that releases quota. The destroy path deletes the request row entirely. Escalate-failure handling: if `decision_store.create` raises during escalation, the request is marked `failed` (terminal) instead of being stranded in `requested`.
- Agent terminal conduit (D1): new `tinyagentos.tuiui_conduit` module is a synchronous client for the tuiui apphost Unix socket, speaking newline-delimited externally-tagged JSON per `docs/design/taos-tuiui-spike-findings.md`. Operations: `connect` (default path `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock`), `Spawn` (cmd/args/cwd/cols/rows -> AppId + pid), `send_input` (raw bytes as integer array, not base64), `list_apps` (Roster), `kill`, `set_meta`, `shutdown`, `iter_frames`, `frame_lines` (ANSI-free grid -> text), and `rebind_by_meta` for meta-based AppId recovery across daemon restarts. New tests under `tests/test_tuiui_conduit.py` exercise the client against an in-test stub apphost (no real tuiui binary required in CI): 21 tests cover spawn round-trip, input byte encoding on the wire, frame-to-text reconstruction, meta-based rebind after a simulated apphost restart with the AppId counter reset, concurrent frame/reply demultiplexing, socket ownership refusal, and grid geometry handling.
- A single background reader thread now owns the conduit socket and demultiplexes incoming events into a frame backlog and a reply backlog. Previously `iter_frames()` and the request/response calls both called `recv()` unsynchronised on the same socket, so a frame consumer could swallow (and silently discard) a `Spawned`/`Roster` reply another thread was waiting on, and a request waiter could discard a `Frame` that arrived before its reply — either way the loser blocked until timeout. Frames dropped because a consumer fell behind `frame_backlog` (default 512) are counted in `TuiuiConduit.dropped_frames` rather than lost silently.
- `connect()` verifies the apphost socket before speaking to it: the socket and the directory holding it must be owned by the calling euid and must not grant group/other access, and the socket must not be a symlink. Because a pathname can be swapped between the check and the connect (CWE-367), the connected peer's uid is then read from `SO_PEERCRED` and must also match, and the check fails closed — a platform that cannot report peer credentials is refused rather than trusted. `connect()`/`close()` run under a dedicated lock so racing callers cannot leak a second socket and reader thread. Without `XDG_RUNTIME_DIR` the default path is now keyed on the numeric uid instead of `$USER`, which any local user could predict and pre-create a listener for (CWE-377). `connect()` also wraps connection failures in `TuiuiConduitError` instead of leaking raw `OSError`.
- Grid parsing no longer loses or invents cells: a flat `cells` list with no `cols` is read as one row instead of being assumed 80 wide, a partial final row is kept via ceiling division rather than dropped, and `Frame.cells` is normalised to exactly `rows * cols` with the new `Frame.truncated` flag reporting a grid that did not match its declared geometry — so a short row is distinguishable from a blank one instead of being quietly stripped by `frame_lines()`.
- Values arriving on the wire are coerced through a checked converter, so a malformed grid geometry, cursor, `flags`, `switch_to`, `Spawned` or `Roster` field raises `TuiuiConduitError` rather than a bare `ValueError`. Replies dropped because the reply backlog filled are counted in `TuiuiConduit.dropped_replies`, mirroring `dropped_frames`.
- A request that times out marks the conduit desynchronised, and every later request refuses until the caller reconnects. The apphost does not echo `req_id` on replies (`Spawned` carries only `app` and `pid`), so a reply that arrives after its own request gave up is indistinguishable from the next request's reply, and accepting it would hand the caller a stale AppId to send input to or kill. The refusal happens in the single send chokepoint, before the command reaches the wire, so a refused `Spawn` cannot leave behind an app the caller has no id for and a refused `Kill`/`Input`/`SetMeta` cannot act on an AppId the desync made untrustworthy. Frames are unsolicited and uncorrelated, so `iter_frames()` keeps working; only it, `close()` and `connect()` stay available.
- A grid's declared geometry is bounded before it is padded. `cols` and `rows` arrive on the socket, so `cols * rows` was an allocation the apphost chose for this process: a one-cell frame declaring `{"cols": 1000000, "rows": 1000000}` made the parser build roughly 10^12 list entries and exhaust memory in the consumer thread. A geometry more than 1 MiB of cells beyond what was delivered is now refused as a malformed frame, on both the flat `cells` and the `rows_list` shapes.
- Every wire record shape is checked before it is dereferenced, so a malformed payload raises `TuiuiConduitError` like the rest of the module instead of a bare `KeyError`, `TypeError` or `AttributeError` that a caller catching `TuiuiConduitError` cannot handle: a `Roster` that is not a list (a dict or a string would otherwise have been parsed from its keys or its characters), a roster entry that is not an object or carries no `app`, non-list `Roster.args`/`Roster.meta` (`rebind_by_meta()` iterates the latter), a `Spawned` payload that is not an object (`"app" in ["app", "pid"]` is also True, and `spawn()` then subscripted a list by name), a `Frame` payload or `Frame.grid` that is not an object, and non-list `Frame.images`/`Frame.image_data`.
- The reader now refuses a single wire event past a 32 MiB ceiling. The apphost frames events with a newline, so until one arrives the bytes had nowhere to go but an unbounded read buffer: a peer that never sent a newline, or one that genuinely delivered a billion cells, sized an allocation in this process that no downstream check could undo, because `json.loads` had already built the list. The buffer is the one place every byte passes through, so that is where the bound now lives; the grid-geometry bound above remains the separate guard against a *small* payload declaring a huge one.
- Dispatcher now permanently parks a card after `STRIKE_THRESHOLD` cumulative failed dispatches (releases), instead of quarantining it. Strikes are cumulative with no time window.
- Security: the SSRF guard now pins each outbound connection to the address it
  validated. Fetches of user-supplied URLs (browser proxy, extract, download,
  Library web ingest, Knowledge article ingest, peer handshake delivery,
  UnifiedPush) go through a guarded client whose connections resolve and check
  the hostname once and connect to that answer, so a low-TTL nameserver can no
  longer answer public to the check and 127.0.0.1 to the connection. TLS
  verification is unchanged and still validates the original hostname.
- Fix: Library web ingest now reuses a single guarded client across an entire
  redirect chain instead of building and tearing down a fresh one (new
  connection pool, SSL context, pinned backend) on every hop.
- Fix: Knowledge article ingest now rejects a caller-supplied `fetch_client`
  that is an `httpx.AsyncClient` but was not built by `guarded_async_client`,
  instead of silently accepting an unguarded client and bypassing the SSRF
  pin.
- Added endpoint tests for tinyagentos/routes/mail.py to verify account CRUD, folder/message listing, and SMTP send operations
- Reaper now reaps `executor.sh` processes older than CAP regardless of contained CLI, as an additional third rule alongside the existing CLI and orphan rules. This catches hung lanes whose parent process (e.g. dispatch_loop) is alive but neither a CLI nor orphaned.
- taOS Pocket prototype for rabbit r1: a 240x282 card-stack creation with
  agent sessions, notifications, and decision screens, mock-mode data layer,
  and inline vanilla-JS for sandboxed devices.
- **Gate-integrity token env propagation test**: Exercises `main()` with no `--token` and `GITHUB_TOKEN` set in the environment, asserting the API layer receives the env token so unauthenticated and authorized calls are distinguishable.
- **Protected-path regression tests**: Parametrized `TestIsProtected` cases for `docs/doc-gate.toml`, `pyproject.toml`, and `tests/conftest.py`, each of which fails if the path is removed from `PROTECTED_PREFIXES`.
- Library app ingest queue view: a pane listing active and failed pipeline jobs with stage, error text, and a per-job retry action. Polls the jobs endpoint every 3s while jobs are active and stops once the queue is idle (retry POST mocked until #2058).
- Native decision push payloads now carry a rich image attachment and the full approve / reject / add-note action set so iPhone and Apple Watch can render the buttons and the native shell can wire the callback to the Decisions answer route (tsk-cf7wzc).
- APNs payloads set `aps.category`, and set `aps.mutable-content` whenever an action set or image is present on a non-silent push, so the iOS service extension can attach the image and surface the registered `UNNotificationCategory` buttons. A silent (`content-available`) push keeps its background delivery and is left unmutated.
- Web-push payloads now include the optional `image` on both the top-level `Notification.image` field and the inner `data.image`; clients that ignore `image` still receive a valid text notification.
- Per-agent desktop lifecycle routes: install XFCE + x11vnc into an existing agent LXC on demand, plus start, stop, and status endpoints under `/api/agents/{agent_name}/desktop/*`.
- Agent state versioning: a git repo is initialised inside each agent container at deploy time, with a `.gitignore` that excludes secrets and bulk artefacts, and commit identity set to the agent's own slug. An auto-committer script runs as a background loop inside the container, committing dirty trees on a fixed interval with a timestamp + changed-file-summary message (#tsk-fjmxzo).
- Controller API for agent state history: `GET /api/agents/{name}/versions` lists commits, `GET /api/agents/{name}/versions/{sha}/diff` returns the patch for a commit, and `POST /api/agents/{name}/versions/{sha}/revert` reverts the state repo to a prior commit (#tsk-fjmxzo).
- taOStalk mobile PWA (chat-pwa) now ships a bottom tab bar with Chats,
  Projects, Decisions, and Agents. The Chats tab keeps the user on the
  in-place MessagesApp; the other three deep-link into the desktop shell
  via `/desktop?app=<id>` so the chat PWA stays a focused comms surface
  without re-implementing sibling platform apps. The bar is hidden on
  desktop viewports and never overflows horizontally on a phone-size
  viewport, so the new tabs do not regress the existing single-column
  message view or the `MobileSplitView` back-nav.
- Library P4 research spike eval harness: `scripts/library-vmaf-eval.sh` and `scripts/library-vmaf-eval.ps1` compute VMAF per (source, variant) pair via ffmpeg libvmaf and emit CSV (video, variant, vmaf_mean, bytes_source, bytes_variant, saving_pct). Four 1-second 320x240 fixture clips under `tests/fixtures/` are included for local and CI runs (#tsk-gyrts2).
- LoRa off-grid transport design note (docs/specs/tsk-ha5iau/2026-08-28-lora-off-grid-transport-design.md): Security model for Meshtastic bridge, message schema fitting 237-byte LoRa payload, allowed A2A kinds, and concrete first milestone for point-to-point prototype with two Heltec V4 modules.
- `scripts/check_evil_merge.py`: gate that detects evil merges in test files by comparing the merge result blob against the `git merge-tree --write-tree` baseline and failing when the resolution differs from what git would have produced automatically. Runs on pull requests targeting `master` or `dev` via `.github/workflows/evil-merge-gate.yml`.
- `scripts/check_evil_merge.py`: hardened against an octopus merge (3+ parents), which now refuses to pass instead of silently comparing only the first two parents, and against a merge that deletes a test file both parents kept, which is now flagged as a violation instead of being invisible because it is scoped to paths present at head.
- New `schema-column-guard` static check (`scripts/check_schema_column_migrations.py`) plus matching doc-gate step; flags any column added to a `CREATE TABLE` inside a store's `SCHEMA` with no matching `ALTER TABLE ... ADD COLUMN` in the same file, including the previously-uncovered case of zero migration at all (proven on PR #2416).
- taosgo: `POST /api/taosgo/app-join` route scaffold, CSRF-protected, authenticating via session cookie or app-password Bearer token; the Headscale preauth key it returns is a placeholder until the 2FA login split (tsk-m7ufkp) lands and the route joins the 2FA-required set.
- `mcp`: the supervisor now drains an MCP server's **stdout** as well as its
  stderr. Both pipes were captured but only stderr was ever read, so any server
  writing more than the 64 KiB pipe buffer to stdout blocked in `write()`
  forever while the supervisor kept reporting it healthy — and for a
  stdio-transport server stdout is the JSON-RPC channel, so that was the
  primary data path, not an edge case. Log entries now carry a `stream` field
  (`stdout`/`stderr`), and the Logs tab tails both.
- `mcp`: both drains read the pipes in fixed-size chunks instead of iterating
  lines. `async for line in reader` raises `ValueError` once a single line
  exceeds `StreamReader`'s 64 KiB limit, which killed the drain task and
  re-opened the same deadlock — a JSON-RPC frame is one line and routinely
  larger than that.
- `mcp`: `POST /api/mcp/call` answers `501 not_implemented` while the JSON-RPC
  transport is unwired. It used to answer `200` with
  `{"ok": true, "result": "stub ..."}`, which no caller could tell apart from a
  real tool result.
- UnifiedPush publisher for Android devices: `tinyagentos/push/unifiedpush.py` provides `HttpUnifiedPushSender` and `NullUnifiedPushSender`, sharing the `ApnsSender` protocol via `tinyagentos/push/__init__.py`. The dispatch seam in `notifications_push.py` routes notifications to APNs or UnifiedPush by looking up each device's `platform` column, with no platform conditionals at call sites. Decision notifications now include action metadata (approve/deny/pick-option/quick-reply) in the push payload for Android clients. `PATCH /api/devices/{id}/push-token` accepts URL-shaped tokens for `platform=android` devices. (#tsk-jayme6)
- `scripts/check_merge_attribution.py`: reconciliation check that reads the fleet's local JSONL merge audit log and asserts every merge commit in the repo has a matching audit entry, failing red with the unmatched SHA when one is missing.
- `scripts/gate_merge.sh`: fleet merge wrapper that appends an actor, repo, PR, SHA, merged_by, timestamp, and script entry to `~/.fleet/merge-audit.jsonl` after every successful `gh pr merge`, giving each fleet action an append-only local audit trail without changing the GitHub `merged_by` field.
- `tests/test_merge_attribution.py`: three acceptance proofs — a merge with no audit line goes RED naming the unmatched SHA, a normal fleet merge with an audit line reconciles clean with rc=0, and deleting the audit line for a real merge returns to RED, proving the check is load-bearing.
- Wake-budget config: per-agent and per-project scheduled-wake overrides with a global default of 2/day, OS-enforced by the heartbeat loop and surfaced in Observatory and via `/api/agents/{name}/wake-budget`.
- Agent scope requests are now readable, not only writable:
  `GET /api/agents/registry/{canonical_id}/scope-requests` lists an agent's
  scope requests (optionally filtered with `?status=pending|accepted|refused`)
  and `GET /api/agents/registry/{canonical_id}/scope-requests/{req_id}` reads
  one. Previously the only handle on a pending request was the `request_id`
  embedded in the notification payload, so dismissing the notification left the
  request alive in the store but addressable by nobody — a failed approval was
  indistinguishable from a successful one, and requests silently filling the
  pending cap could not be inspected.
- Both reads are authorized exactly like create: the agent's own registry
  bearer token, or the owning user / an admin. Every other caller — including a
  different agent's token and an authenticated non-owner — gets the same
  existence-hiding `404 {"detail": "agent not found or not active"}` the
  neighbouring create/approve/deny routes return, so nobody can enumerate
  another user's agents' requests or use the route as an existence oracle.
- Both reads return an explicit public projection of the stored row. The
  deciding owner/admin's user id (`decided_by`) is withheld — the agent's own
  token may read these routes, and no other agent-reachable route discloses its
  owner's internal id — while `status`, `decided_ts` and `granted_scopes` keep
  the decision fully observable. The list response is bounded at 200 rows:
  pending requests are taken oldest-first and ahead of everything else, so the
  stranded request these reads exist to recover is the last row a full page
  drops, and decided requests fill the remainder newest-first. Each group is a
  separate indexed query with its own `LIMIT`, so read cost is bounded by the
  page size rather than by how much decision history the agent has
  accumulated. The `?status` filter accepts any casing.
- Additional fix: make the undeploy_agent function actually remove the trace dir when delete_state=True, matching the docstring.
  While the current code removes agent workspaces and memory, the trace directory is also created by the deployer and should be cleaned up with the agent. The PRAGMA journal_mode=WAL is added directly in _SCHEMA (which is correct), and busy_timeout pragmas are added for consistency with other stores.
  Also added an eviction mechanism to the SpanStoreRegistry to keep the registry size bounded with an LRU implementation.
- WAL mode journal pragma to browser_sessions.py
- Trace directory removal in undeploy_agent
- GET /api/library/jobs returns the cross-item ingest job list, honouring ?state= and ?limit=.
- POST /api/library/jobs/{id}/retry re-queues a job in error state and returns 404 for unknown ids or 409 for non-error jobs.
- Added a `skip_if_no_embed_backend` pytest marker for tests that genuinely cannot run without an embedding backend. The gate probes the capability itself — a socket to the packaged default qmd URL, or an installed `onnxruntime` — rather than an environment variable no product module reads. Nothing carries the marker today, and `tests/test_embed_backend_marker_debt.py` asserts that list stays empty so it cannot become a way to turn a red test green.
- Post-merge audit of PRs #2710 (ready_tasks blocked-on label join + limit clamp), #2709 (reaper hung-lane detection), and #2704 (board Unquarantine button keyboard a11y + quarantine column). Fix-forward card #2758 cut for the #2704 finding: drag-and-drop onto quarantined cells in lanes view silently swallows the drop with no user-visible feedback.
- Launcher tier filtering in `getLaunchableApps`: tier 1 and tier 2 apps surface in the launcher; tier 3+ and handler apps are excluded.
- Exported `APP_REDIRECTS` map and `resolvePinnedId` so dock pin-restore can resolve legacy or renamed app ids before treating them as orphaned.
- Engine selection support for BaseStore: Added Engine enum (SQLITE, POSTGRES) to allow stores to specify which database engine to use. Stores now have an optional `engine` parameter in their constructor and an `ENGINE` class attribute, defaulting to SQLite for backward compatibility. Added `_init_sqlite()` and `_init_postgres()` methods to BaseStore to handle engine-specific initialization.
- Mark recommended options with a `Recommended` badge and accent border in inline decision cards rendered in chat
- OMP (oh-my-pi) adapter selectable at verification_status experimental via the Agent Client Protocol (`omp acp`) (#tsk-wk7mnf).
- An adversarial-verify stage in the static security analysis pipeline: each finding produced by the code analyzers is now re-examined against its source line to refute false positives inside comments, string literals, or known example values before the findings are surfaced to the user.
- OS-owned task checklist items: `GET`/`POST /api/projects/{project_id}/tasks/{task_id}/checklist-items` (list and create), with store-level create/update/archive in `task_store.py`, activity-feed events, and docs. POST requires the `project_tasks_create` grant; GET takes the default `project_tasks` grant; archive is store-level only and refuses unless the item is both verified and reported (#2674).
- `useOsEvents` hook now shares a single EventSource across all callers in the same browser window, keeping one OS-level SSE connection per client instead of one per app.
- Agent container provisioning request API: `POST /api/containers/requests`
  lets an active agent submit a provisioning request using its own registry JWT.
  A `ContainerRequestStore` (`tinyagentos/container_requests_store.py`) tracks the
  request state machine (requested, approved, pending-approval, provisioned,
  failed), and a `ProvisioningPolicy` (`tinyagentos/containers/provisioning_policy.py`)
  evaluates per-agent quota and threshold from `container_provisioning` config:
  under quota auto-approves, over quota lands in pending-approval, and over
  threshold escalates to a Decisions-app item for Jay. No provisioning happens in
  this slice (P1); the executor lands in P2.
- SSE routing test that drives the real `_notify_emitter` in app.py and asserts user-scoped notifications reach the owner's channel without leaking to broadcast or other users' channels.

### Changed

- taOStalk (Messages) structural parity with the Store app: the
  2834-line `desktop/src/apps/MessagesApp.tsx` monolith now lives at
  `desktop/src/apps/MessagesApp/index.tsx`, and the mobile-aware
  toolbar strip is its own `MessagesApp/MobileMessages.tsx` component
  alongside a `MobileMessages.test.tsx` that covers desktop, mobile,
  selected-channel, and standalone-title cases. `MessagesApp.tsx` is
  kept as a re-export shim so every existing `@/apps/MessagesApp`
  import path keeps resolving unchanged. Behaviour, copy and
  styling are identical to before; this is a move plus a focused
  extraction, not a rewrite (#tsk-iahrh5).
- CSRF is enforced for real in the test suite. The autouse fixture that replaced
  `verify_csrf` with a no-op for every test file whose path lacked the substring
  `test_csrf` is gone; opting out is now the explicit `@pytest.mark.csrf_bypass`
  marker, which nothing uses and which `tests/test_csrf_bypass_debt.py` asserts
  stays unused. The shared `client` fixture echoes the `csrf_token` cookie into
  `X-CSRF-Token` on mutating requests the way the SPA's `taosFetch` does, so
  tests satisfy the real check rather than switching it off. Because the old
  carve-out matched on filename, renaming a test file silently re-armed the
  bypass; that is no longer possible (#tsk-jqcvpc).
- Assistant Studio now matches the rest of taOS. It had been built on raw
  palette colours (fixed greys and a blue accent), so it ignored the active
  theme and looked like a different product next to the App Store and Settings.
  Every surface now uses the shared shell and accent tokens, with the same
  frosted header bar, focus rings and pill badges as the other apps, so it
  follows taOS Dark, taOS Light and any installed theme.
- The "personal assistant" picker's caption now uses the shared field-label
  style, so it reads slightly larger and a little stronger than before. Its
  wording and accessible name are unchanged.
- BaseStore API: Modified `BaseStore.__init__()` to accept optional `engine` parameter. Store subclasses can now specify `ENGINE = Engine.POSTGRES` if they intend to use Postgres in future migration slices.
- `bot-review-gate` enforcement parity is now documented in `.github/workflows/bot-review-gate.yml`
  and the contributor skill: it is REQUIRED on `master` but ADVISORY on `dev` (absent from dev's
  `required_status_checks.contexts`), letting a red check merge through dev and block only at the
  dev->master promotion. The hardening target is to require it on `dev` too; that branch-protection
  edit is Jay's standing GitHub configuration (master is left unchanged) and is not performed by a
  repo commit.
- The desktop client opens `/api/os/events` with `?kinds=` set to the union of every live subscriber's kind list, then applies each subscriber's own list in the browser. Widening into kinds the union already covers no longer reopens the connection, and the union is never narrowed when a subscriber leaves, so it settles at one reopen per distinct kind for as long as at least one subscriber stays mounted (the union resets with the connection when the last one leaves). A reopen overlaps the old and new streams and closes the old one only once the new one is live, so no events are lost across a filter change.

### Fixed

- Cross-user contacts are now keyed on the peer's ed25519 signing-key
  fingerprint rather than on their username, so a peer who changes or reuses a
  username can no longer be confused with an existing pinned contact. Stores
  created before this change are upgraded in place on first open: existing rows
  have their `peer_fingerprint` backfilled from the stored public key, and rows
  whose key is missing or malformed are left unkeyed instead of aborting the
  upgrade. Revocation now reports how many contacts matched and cascades to
  every contact sharing the revoked fingerprint, and blocking a peer cascades
  consistently through both block paths (#2561).
- Auth routes now answer `400` instead of `500` when a JSON request body parses but is not an object. `request.json()` accepts `null`, `[]`, `1` and `"x"` as valid JSON, and the subsequent `body.get()` raised `AttributeError`. All six affected routes (`POST /auth/login`, `/auth/setup`, `/auth/complete`, `/auth/users`, `/auth/users/{username}/profile`, `/auth/users/{username}/password`) now share one `_json_object()` helper. Three of them are session-exempt, so the fault was reachable without credentials.
- `scripts/check_gate_integrity.py` no longer dies on a slow `git`. `_detect_repo()` runs `git remote get-url origin` with `timeout=5`, but caught only `subprocess.CalledProcessError`; `subprocess.TimeoutExpired` descends from `SubprocessError` instead, so a timeout escaped as an uncaught traceback. That exits `1`, and `EXIT_BLOCKED` is also `1` — so a merely slow `git` was indistinguishable from "this PR edits a protected gate file" and blocked an innocent PR instead of falling back to the documented default. The handler now catches `subprocess.SubprocessError`, which covers both.
- `_detect_repo()` strips a trailing `.git` as a suffix rather than globally. `.replace(".git", "")` also removed the marker from the middle of a repository name, so a remote for `acme/widgets.git.archive.git` resolved to `acme/widgets.archive` — a repository that does not exist. Inert for `jaylfc/taOS`, which is why it survived; it only shows on a name carrying an embedded `.git`.
- `.github/workflows/gate-integrity.yml` now re-runs on the `edited` activity type, closing a base-retarget bypass. The gate's verdict is a function of the base..head diff and the `gate-integrity-allow` label, but retargeting a pull request's base branch fires `edited` (carrying `changes.base`) and no other activity type — so the gate never re-inspected the new file list. Because `dev` runs loose branch protection (`strict: false`), the head SHA does not move on a retarget and nothing else forced a re-run: a PASS earned against one base kept satisfying the required check against another whose diff touches protected gate files. `edited` also fires on title and body edits; the extra runs are cheap and read-only, which is the right trade against a silent bypass. A live regression guard parses the workflow and fails if any verdict-changing activity type (`opened`, `synchronize`, `reopened`, `edited`, `labeled`, `unlabeled`) is dropped.
- The deleted-symbols gate no longer leaks modules into `sys.modules`. `_resolve_symbol`
  executes the module under inspection, so its transitive imports were loading out of the
  merge tree and staying resolvable under their real names; its tests also purged
  `tinyagentos.*` without restoring it. Both are now restored exactly, so a later
  `mock.patch` target cannot resolve to a different module object.
- Notification archive tab now merges store-derived rows with fetched server-only rows instead of replacing, so server-only archived rows survive store mutations and clearAll.
- In-flight archive fetch spinner no longer drops early on rapid tab toggles when an aborted request's finally fires after a newer request has started.
- The Notifications settings panel no longer duplicates its `<section>`/`<h2>`/description chrome across the loading, error, and loaded render branches; the chrome now lives in a single return, and the loading state's heading spacing unifies with the error and loaded states (the loader was the `mb-5` outlier).
- Replaced bespoke bridge design with direct Meshtastic connector using existing channel_hub routing infrastructure, eliminating rejected bridge service, A2A bus injection, and @bridge-<id> handle while preserving security model
- `InvalidContainerTargetError` (malformed agent name or remote) is now caught in all agent version routes, returning 400 instead of 500.
- Agent state revert now uses dedicated `DirtyTreeError` and `NotAncestorError` exceptions instead of string matching.
- `git rev-parse HEAD` return code is now checked in `git_revert`.
- Agent version routes now enforce owner-or-admin authorization on list, diff, and revert operations.
- Cross-process lock serializes state writers (committer and revert) to prevent lost commits.
- Committer startup failures are now reported as `committer_failed` steps.
- A stale non-device `Authorization: Bearer ...` header no longer returns 401 before the session cookie is consulted; authenticated browser requests with a leftover registry token now reach the route as `via="session"`. The deferred 401 still fires when no `taos_session` and no valid credential authenticate the request.
- Per-agent local-token bindings store now serialises read-modify-write cycles
  under a process-shared file lock, preventing concurrent deploys from dropping
  one another's binding. Corrupt or mis-shaped bindings files cause
  `bind_local_token_agent` to raise rather than silently resetting the map to
  empty and overwriting it; `validate_local_token` and `get_local_token_agent`
  treat a non-dict bindings file as having no bindings instead of raising.
- Cross-user event leakage via EventBus broadcast channel. Events with a `user:<id>` target are now routed only to that user's channel instead of being published to broadcast, preventing one authenticated user from seeing another user's events through `/api/events/stream` and `/api/os/events`. Notifications scoped to a specific user are also routed to the per-user channel; system-wide notifications (no `user_id`) continue to use broadcast.
- Agents can be named in Chinese, Japanese, Korean, Cyrillic, Greek, Arabic, Hebrew or Thai. Such a name was rejected with "Agent name must contain at least one letter or number" because the slugifier deleted every non-ASCII character before checking whether anything was left; names are now transliterated, so each gets its own distinct slug. Accents fold to their base letter instead of being dropped ("naïve résumé" was `na-ve-r-sum`, now `naive-resume`).
- Two agents whose names produced no slug no longer share an identity prefix in the agent registry. The `"agent"` fallback gave every such name the same slug; the fallback is now derived from the name itself, so the canonical ids stay distinct. Creating a project from a consent request has the same fix.
- Deduplicating an already-63-character agent slug no longer overruns the container-name limit the truncation exists to respect.
- The unslugifiable-name fallback slug uses a wider digest (8 bytes instead of 4), closing a collision window that could resolve a slug lookup (e.g. a DM channel member) to the wrong agent.
- The OS-level project-invite redeem handle no longer drops the harness when the display name is unslugifiable but the label slugifies -- two different harnesses no longer collide on the same label-only handle.
- The Deploy and Import wizards no longer claim taOS will derive a slug for a name with no Latin letters; those two forms reject such a name server-side (no fallback), so the hint now points at the manual "edit" control instead.
- Folded CodeRabbit findings on #2763: collapsed the duplicated deadline checks in the port-open and ready wait loops down to one guard before each probe and one after, floored `curl --max-time` at 1 s so a future guard edit cannot disable the per-attempt timeout, anchored the test assertions to each phase, and corrected the `tsk-wgsns5` changelog wording (per-probe timeout and cold-boot timing)
- Anchored each installer wait phase to an absolute deadline (`_port_deadline` / `_ready_deadline`) and re-read the clock after every probe, so a slow `curl` can no longer let the follow-up `sleep` carry the port-open or ready phase about a second past `_PORT_WAIT` / `_READY_WAIT`
- Knowledge ingest (R2-26) now chunks at sentence boundaries with ~200-char
  overlap instead of fixed 2 000-char slices, deletes an item's old QMD chunks
  before re-embedding so stale chunks cannot accumulate, and reports a `partial`
  status (instead of silently `ready`) when some chunks fail to embed.
- Broadcast notifications now have per-user read/archived state to prevent cross-user state leaks. Previously, when one user marked a broadcast notification as read or archived it, it affected all users' inboxes. Now each user's read/archived status is tracked independently in the `notification_user_state` table, preserving individual inbox states while maintaining the shared broadcast nature of the notification.
- Fixed `unread_count` query missing table alias and `list()` query missing per-user archive filter so broadcast state is correctly scoped per user.
- Path traversal (CWE-22) in model archive promotion: a crafted `model_id` such as `../victim` can no longer make `model_files_dir` resolve outside the archive root. The promotion engine now validates `model_files_dir` against `archive_root_path` before moving files.
- Registration payloads with `"ram_mb": null` no longer propagate `None` into `WorkerInfo.hardware`, preventing `TypeError` from `ram_mb // 1024` in `worker_tier_id` when `list_workers` is called.
- Apple push no longer mints a fresh provider token for every notification: one token is now cached and reminted on a 50-minute timer, so a burst of notifications can no longer trip Apple's `TooManyProviderTokenUpdates` cap and get pushes refused for the whole account (tsk-42q2qf).
- A `410 Unregistered` from Apple now clears the dead push token from the device instead of being reported as a generic delivery failure, so an uninstalled or re-provisioned device is no longer pushed to forever. The device itself stays paired and visible; it simply has no push token until it registers a new one.
- Every refused push now logs Apple's own `reason` and the `apns-id`, so a refusal can be diagnosed instead of appearing as an unexplained non-delivery. An expired provider token also forces an immediate remint rather than waiting out the refresh timer.
- `InvalidProviderToken` (a rotated signing key, or an otherwise-unparseable cached token) now also forces an immediate remint, the same as `ExpiredProviderToken`, instead of refusing every push for the rest of the 50-minute cache window.
- The cached provider token's `iat` can no longer regress after a backward wall-clock step (a bad NTP correction): it is floored at the previous `iat`, so Apple's own clock cannot see the token as older than the real elapsed time and reject it before this cache's refresh timer would have fired.
- `workflow_dispatch` removed from `.github/workflows/gate-integrity.yml`. With it present, any write-access actor could dispatch with `ref=<own branch>` and `pr_number=<benign PR>` to publish a spoofed green check run named `Gate integrity` against a chosen head SHA, bypassing a required status check. `pr_number` was also interpolated raw into the `run:` block (the `type: number` annotation is not enforced on API dispatch, making it an injection sink). Re-runs now happen via `gh run rerun` on the original event or the `labeled` re-fire.
- `is_protected()` now matches the file *basename* against `scripts/check_*.py` instead of substring-searching the full path for `/check_`. The old check returned `True` for `scripts/check_helpers/util.py` because the directory name `check_helpers` contained `/check_`, contradicting the `scripts/check_*.py` contract and leaving `GATE_SCRIPT_PREFIX` as dead code.
- `test_gh_failure_does_not_silently_pass` now extracts the gate workflow's actual `run:` block and executes it, replacing the hand-copied bash string that never reflected workflow edits and stayed green even when the step was deleted.
- `scripts/check_doc_gate.py`: a workflow change that bumps only `uses: <action>@<ref>` pins no longer trips the `contributor-skill` doc-gate rule, so dependency-update PRs for GitHub Actions can turn green on their own instead of stalling red until a maintainer force-pushes a `Docs-Reviewed:` trailer that the bot cannot author. The exemption is content-based: a substantive workflow edit by any author still fails the gate.
- Rate-limit windows no longer allow twice the documented burst at the window
  edge. The 20-per-10s limiters reset their counter on the first request after
  the window elapsed, so 20 requests just before the boundary plus 20 just
  after went through in a fraction of a second; the shared limiter now counts
  over a moving window, which halves the reachable brute-force rate against
  the invite PIN.
- Rate-limit windows no longer freeze when the system clock steps backwards.
  They measured elapsed time with the wall clock, so an NTP correction after a
  cold boot on a board without an RTC could lock a caller out until the clock
  caught up. Every limiter now uses the monotonic clock.
- `429 Too Many Requests` responses now carry `Retry-After`, so a client that
  is throttled can back off instead of retrying as fast as it can fail.
- `RateLimiter` and `MovingWindowLimiter` now reject a non-positive `max_keys`
  at construction with `ValueError` instead of raising `KeyError` out of the
  eviction loop on the very first tracked key.
- `RateLimiter` now rejects a non-positive `capacity` or `refill_per_second`,
  and `MovingWindowLimiter` a non-positive `max_per_window` or `window_secs`,
  at construction with `ValueError`. Previously a bad value silently locked
  every caller out forever (`capacity=0`) or disabled the limiter entirely
  (`window_secs<=0` accepted every request); `Retry-After` also no longer
  advertises a made-up 1-second retry for a bucket configured to never
  refill, since that configuration is now rejected up front.
- `scripts/check_bot_review.py` now skips runner-owned GitHub Actions job check
  runs (those with a non-null `external_id` or a `details_url` under
  `/actions/runs/`) during reconcile and verdict reads. The Actions API token
  cannot PATCH these runs, so a stale job-owned FAILURE previously caused
  reconcile to fail closed and pin every subsequent gate run red. Job-owned
  staleness is irrelevant because GitHub's merge box keys off the latest check
  run per name, and only the script's own runs are writable.
- `test_queue_position_global_for_loads` and `test_queue_position_per_model_for_inference` in `tests/test_gpu_arbiter_queue_ops.py` settled on a single task before asserting queue positions for all submitted tasks, racing the event loop when later coroutines had not yet enqueued. The settle predicates now wait for every asserted task_id to be non-None, eliminating the flaky `assert queue_position(t2.id) == 2` (None vs int) failure (#tsk-4k7i4j).
- `scripts/check_bot_review.py` now matches both the `Bot review gate` workflow
  display-name check run and the `bot-review-gate` job-id check run (the
  runner-owned run GitHub creates for the Actions job itself), so
  `list_check_runs` no longer drops the run that actually pins a self-healed PR
  on `mergeStateStatus: UNSTABLE` per the #2573 lead-block evidence.
  `check_run_verdict` is wired into `main()` as the head-SHA read side of the
  #2493 reconcile (it was previously defined but uncalled), and the suite gains
  `test_stale_failure_matched_by_job_id` (a `bot-review-gate`-named fixture that
  fails on the original line) plus a `Bot review gate`-named control so a fix
  that swaps one name for the other is caught rather than covering both.
- Consent-approve handle collision guard is no longer blind to internal driver identities: `POST /api/agents/auth-requests/{request_id}/approve` now chains an exact-then-normalised handle lookup, and an external-selfjoin claim that collides (via normalised handle) with a `taos-internal` or `taos-deployed` active identity fails closed with 409 instead of minting a duplicate identity or reusing the foreign identity's `canonical_id`.
- The Phase 4 reasoning judge sent the placeholder key `taos-internal` to the LiteLLM proxy on every call, so `litellm_auth` rejected it with 401 and the judge has been dead since introduction. `app.py` now passes the real per-install master key (`get_litellm_master_key`) — the same source the deployer and LLMProxy use — and `ReasoningJudge`'s `litellm_api_key` parameter is required (the `"taos-internal"` default is removed) so a missing key fails loudly at construction instead of silently producing a dead judge.
- The Postgres-before-cloud store assessment no longer ships two
  contradictory per-store classifications that disagreed on 37 of 78 stores,
  plus six raw grep dumps and a root-level duplicate. It is consolidated into a
  single verified reference at `docs/design/store-classification-reference.md`
  enumerating all 79 `BaseStore` subclasses (including the previously-omitted
  `ProjectListEntriesStore`), with the rule each of the three columns encodes,
  credential-bearing stores called out first, and the total derived from the
  table row count rather than asserted.
- Projects board: `PATCH /api/projects/{pid}/tasks/{tid}` no longer reports success for an edit it drops. Clearing a card's assignee, parent or element (sent as `null`, e.g. dragging a card to the board's "Unassigned" or "Orphans" lane) now persists, and a field the route cannot write — a `null` on a non-nullable field, a misspelled key, or a read-only column such as `id`/`created_by` — is rejected with 422 instead of answering 200 with the unchanged task.
- APNs push payloads no longer let a caller-supplied `data["image"]` silently replace the explicit `image` argument: explicit arguments now win over `data`, and `aps.mutable-content` is set from the image that actually lands in the payload, so the notification service extension never fetches an image the flag was not computed for (tsk-674fwg).
- `aps.mutable-content` likewise follows an action set supplied through `data`, so decision buttons threaded that way are no longer dropped by the extension.
- A stray `data["aps"]` can no longer overwrite the `aps` envelope built from the explicit push arguments.
- An explicit `actions=[]` argument now takes precedence over a stale `data["actions"]` list, instead of being treated as omitted and silently overridden.
- An explicit `actions` argument (including `[]`) now also overrides `payload["actions"]` itself, not only the `aps.mutable-content` gate, so the stale `data`-supplied action set can no longer leak into the payload the client actually receives.
- Fire-and-forget asyncio tasks in `ClusterManager` and the `/api/agents` deploy
  route are now kept alive via a new `_spawn_background_task` helper that holds
  a strong reference in `_background_tasks` (asyncio recommended pattern).  Tasks
  created inline without a reference can be garbage-collected mid-flight, which
  previously left agents stuck in `"deploying"` forever.  The done-callback now
  logs exceptions instead of silently discarding them.
- MessagesApp sidebar presence now assembles bound channels through the shared `collectBoundChannels` helper, and the standalone project-channel regression test binds to that call instead of a hand-built copy, so it genuinely covers the production path
- Fixed checklist item attribution: added `created_by` column to `task_checklist_items` table and persisted it when creating checklist items
- Reduced exception handling granularity in `tinyagentos/routes/observatory.py` to follow secure coding best practices
- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` size-budgets the `_truncated` marker so it cannot itself breach the 32768-byte cap, appends every later removal to the dropped count so the marker stays accurate, and never returns a snapshot over the limit.
- Cross-user notification read and mutation: `list`, `list_archived`, `unread_count`, `mark_read`, `archive`, and `mark_all_read` now scope to the authenticated user (`user_id IS NULL OR user_id = ?`), so a user can only see and modify their own notifications plus broadcasts. Previously these endpoints returned every user's rows and allowed cross-user mutations (CWE-862).
 - Notification routes resolve the caller from `request.state.user_id` instead of a cookie-only dependency, so local-token (`taosctl notifications`) callers resolve to the primary user and keep working instead of returning 401.
- The UnifiedPush SSRF guard now correctly blocks the CGNAT (100.64/10) range, which our own A2A bus lives in. Previously, when `send()` called `validate_url_or_raise` with `allow_private=True`, the guard's `_BLOCKED_NETWORKS` check was bypassed, allowing private and CGNAT addresses.
  Now, CGNAT addresses are always blocked regardless of `allow_private`, while other private addresses (RFC1918) are still permitted when `allow_private=True` as intended. This fixes the SSRF vulnerability where devices could target internal infrastructure.
- `load_or_create_signing_keypair`: concurrent reader can see an empty key file when two processes race; fixed by using `filelock` around generate-or-load and `atomic_write_bytes` (tmp + rename, mode 0o600) for the write, so either the old key or the new key is always visible atomically.
- Use `readability-lxml` for HTML extraction in both `knowledge_ingest.py` and `library_pipeline.py`
- Extract text properly handles HTML entities with `html.unescape()`
- Remove the 100-character threshold for readability extraction
- Fix regex pattern that broke when `>` appeared inside HTML attributes
- Maintain fallback to simple tag-stripping when `readability-lxml` is not installed
- `download_file` in `tinyagentos/installers/download_installer.py` now pairs
  `proxy` and `trust_env` structurally instead of relying on callers remembering
  to pass `trust_env=False`: `trust_env` defaults to `None` and resolves to
  `False` when an explicit `proxy` is given (so an ambient `HTTPS_PROXY` env
  var cannot silently override the caller's explicit choice), while remaining
  `True` for the no-proxy path used by `hf_multi_installer`. An explicit
  `trust_env` always wins (#tsk-ay6za5).
- Config validation now rejects non-integer values (floats, bools) for global_default, per_agent, and per_project budgets. Previously `0.9` was accepted and truncated to `0`, silently disabling scheduled wakes fleet-wide.
- Damaged wake_budget.json state now reports unknown/damaged state (consumed:0, remaining:0) instead of full budget (consumed:0, remaining:budget) when can_wake returns False.
- Fleet wake-info now preserves the first successful read when the second read fails, maintaining consistency across the two read surfaces.
- Bare/empty `wake_budget:` YAML keys (parsed as ``None``) were supposed to be tolerated as defaults here but the code change did not actually land (the AppConfig constructor still received ``None`` and ``validate_config`` rejected it). The actual fix ships separately; see the tsk-oenmo2 changelog fragment.
- YouTube and X fetchers now resolve `yt-dlp` via `shutil.which` and raise a named error when it is not installed. Subprocess calls use `--dump-single-json` with `decode(errors="replace")`, track processes per request with cleanup on exit, enforce a per-subprocess timeout via `asyncio.wait_for`, and combine thumbnail and caption download into a single invocation. Caption candidates are sorted deterministically, and thumbnail discovery no longer assumes a `.png` extension.
- **Docstring placement in `check_gate_integrity`**: Moved `token = token or _get_token()` to after the docstring so the docstring is not discarded into a string literal.
- **Pagination error messages in `_api_get`**: Error output now references `page_url` (the actual failing request) instead of the initial `url`, so paginated failures report the correct endpoint.
- Cluster manager hardening: `_format_hw` coerces non-integer `ram_mb`/`vram_mb`
  from worker heartbeats instead of raising `TypeError` (500); the register and
  heartbeat routes now reject non-integer hardware fields with `400`.
- Rejected stale-generation (or fenced) worker registrations no longer poison
  `_ever_seen`, so a subsequent valid registration correctly emits the
  `worker.join` notification.
- The worker storage-backup notification path in `routes/cluster.py` now reads
  `app.state.notifications` (the attribute that is actually assigned) instead of
  the never-set `app.state.notif_store`, so the notification fires.
- `ClusterManager.stop()` now drains `_background_tasks` with a 10-second
  timeout so fire-and-forget work completes before shutdown.
- Lease resource allowlist now comes from the worker's scheduler resource inventory (`resources` field) instead of `backends[].name`. This fixes the issue where real claims were being rejected because resource IDs and backend names use different naming conventions.
- Added `resources` field to WorkerInfo and included it in:
  - Worker registration payload (`/api/cluster/workers`)
  - Worker heartbeat payload (`/api/cluster/heartbeat`)
  - SQLite persistence in ClusterManager
  - Controller memory tracking
- Added per-worker lease cap (`max_leases_per_worker: int = 10`) to ClusterManager constructor, counted only for ACTIVE (unexpired) leases per worker.
- Updated `_worker_for_resource()` validation logic to:
  - If worker has non-empty resources inventory, validate resource_id against it
  - If worker has no inventory (older worker), fall back to grammar validation regex
  - This maintains backward compatibility while fixing real claims
- Worker agent now sends `resources` in both register and heartbeat payloads,
  derived from detected backends: always `cpu-inference`, plus `npu-rk3588`
  when an rkllama backend is present, plus `gpu-cuda-0` when a GPU-capable
  backend is present.
- Legacy workers that omit `resources` fall back to the grammar regex and
  emit a WARNING log once per resource check.
- All existing tests in `tests/test_leases.py` pass (37 passed, 0 failed)
- `config.yaml`, the Fernet key that decrypts stored secrets, the hub and mesh credential stores, the GitHub installations file, the wake budget, the Observatory pause state, the store star cache and the beads/canvas exports now survive an unclean power-off, provided their directory already existed. Each was written with a rename that fsynced neither the file nor its parent directory, so a power cut could bring any of them back at the right size and full of NUL bytes — the failure that wiped the account store on 2026-08-21. All of them now go through the crash-safe writer, which also randomises the temp name so two concurrent writers cannot share one temp file. (`atomic_write_bytes` fsyncs only the file's immediate parent directory; a first write that also creates brand-new *ancestor* directories can still lose one of those higher directory entries on a power cut — fsyncing the whole newly created chain is tracked separately. `store_signing.py`'s keypair promotion is a separate, still-unconverted hand-rolled temp-plus-replace and is out of scope for this PR.)
- Two processes sharing a data dir can no longer destroy each other's key material on first boot. The Fernet key and the hub identity keystore were each created with a durable *replace*, so if both processes found the file absent and both generated, the last write won and the loser carried on encrypting (or signing) under a key that was not on disk — every secret it wrote unreadable, and its author fingerprint gone, after a restart. First-time creation now claims the name exclusively and hands a losing process the key that is actually persisted.
- Saving an Observatory pause, throttle or approval-mode setting, and rendering a project's beads or canvas export, no longer block the event loop while the write is fsynced. On a slow disk those syncs stalled every other request and background tick for the duration of the write.
- The no-hard-link fallback in `atomic_create_bytes` (exFAT/FAT removable data dirs) wrote the target file in place, so a crash mid-write could leave it partial or empty and visible to readers — the same failure shape the rest of this PR closes. It now claims a sidecar `.claim` file exclusively and writes the target itself through the durable writer, so the target is either absent or complete; a claim left behind by a crashed writer is detected and reclaimed rather than wedging every future writer, and operators get one warning that the mount has no hard links.
- `hub/identity.py`'s `_save_new` left a pre-existing corrupt keystore (e.g. the 2026-08-21 NUL-filled shape) on disk when the read-back failed to parse, so every boot minted a fresh identity and discarded it. An unparsable read-back is now repaired in place, so one recovery cycle ends with a usable keystore on disk.
- Widened the class-level temp-file guard in `tests/test_config_atomic.py` to flag any name that *contains* a temp word, not just one spelled entirely as `tmp`/`temp`/`temporary` or a whole underscore-separated part — closing the one-word-rename dodge (e.g. `tmp_path` → `intermediate_tmpfile`) one level up. A hand-rolled promotion that must stay outside `atomic_io` is waived in place with `# atomic-io-exempt: <reason>`.
- The no-hard-link fallback's claim winner in `atomic_create_bytes` unconditionally overwrote the target after acquiring the sidecar claim, even if some other route had already landed it durably in the meantime — the claim only decides who may write, not who wins. It now rechecks the target after acquiring the claim and returns the already-persisted bytes instead of clobbering them.
- The rk-llama.cpp installer's `active.alias` write and the store popularity warmer's cache persist each ran their durable (double-fsync) write directly on the event loop, stalling unrelated event-loop tasks on slow storage. Both now run off the loop via `asyncio.to_thread`.
- The no-hard-link fallback's poller in `atomic_create_bytes` could let a bare `FileNotFoundError` escape when the claim owner's own write failed (e.g. disk full) after it acquired the claim: its `finally` still removed the claim, but the target was never created, and the poller's read of the now-absent target raised instead of retrying. It now retries the fallback once, exactly like a stale claim.
- `hub/identity.py`'s corrupt-keystore repair could still race: two processes finding the same corrupt file each minted their own credentials and wrote via a durable *replace*, so the last write won on disk while the other caller returned its own (now-orphaned) creds — signing under a fingerprint that was not actually persisted. The repair is now serialized with a sidecar lock, and a repairer re-checks the file after acquiring it so it adopts a rival's completed repair instead of overwriting it.
- The beads bridge's JSONL render could lose a write on cancellation: `atomic_write_text` runs its fsyncs in a thread-pool worker that cancellation cannot stop, so cancelling a render released its per-project lock while the write kept running, letting a newer render for the same project finish and then be silently overwritten by the older, orphaned write landing after it. The render now waits for its own write to actually land before a cancellation propagates and the lock is released.
- The store popularity warmer's cache persist built its JSON payload from `_star_cache` on the worker thread while a concurrent fetch could still be mutating that same dict on the event-loop thread, occasionally raising `RuntimeError: dictionary changed size during iteration` — silently swallowed by the persist's broad `except`, dropping that tick's write with no visible failure. The payload is now snapshotted on the event-loop thread before being dispatched to the worker.
- `deliver_handshake` now validates each peer endpoint URL through the shared SSRF guard (`validate_url_or_raise`) before POSTing, blocking loopback, link-local, multicast, CGNAT (100.64/10), and RFC1918 targets. It also accepts the normalized dict endpoint form `{"kind", "url", "priority"}` stored by `establish_peer_link`, fixing the `AttributeError` on `.rstrip` that fires when the first caller reads endpoints via `get_peer_link`.
- `update_task` can no longer un-park a card: the parked guard now runs before the
  update-candidate list is built, so `update_task(status="open")` on a parked task
  leaves it parked instead of returning it to the ready pool. The guard is also
  race-free — a status edit will not land on a row parked after the guard read.
- Parking a card clears its claim: `park_task` now nulls `claimed_by`/`claimed_at`,
  so a parked card can never show as held by an agent that can no longer release it.
- Release-time parking is atomic: `release_task` no longer decides on a separate
  pre-read. `park_task(..., only_if_unclaimed=True)` uses a conditional update
  (`status = 'open' AND claimed_by IS NULL`) and its row count as the decision, so
  a claim landing between the strike and the park is never swallowed.
- Board: parked and quarantined cards no longer offer the keyboard move affordance
  (`m`), so the move dialog cannot be opened on a card that can never be moved.
- Board: the Parked column is now actually fed — `useBoardData` fetches
  `status=parked` on load and applies the `task.parked` live event, so a card
  parked by the dispatcher moves out of its old column without a refetch.
- `reopen_task`: drop the dead `AND status != 'parked'` predicate (no row can fail
  it that has not already failed `status = 'closed'`), and cover the real
  invariant with a test that a parked card can be neither closed nor reopened.
- Block path-traversal model IDs in the archive promotion engine so a crafted `model_id` cannot escape the active models root.
- Correct capability-map carry-forward so a worker re-registering with `ram_mb: 0` (or other falsy-but-valid values) updates the stored value instead of silently keeping stale data.
- Guard `gpu` and `npu` fields against non-dict values from older worker agents, matching the existing `cpu` string guard and preventing `AttributeError` crashes on legacy heartbeats.
- Dock restore no longer drops pinned ids for apps not yet registered at restore time. Userspace (.taosapp) pins are preserved in the dock store and re-render once `syncUserspaceApps` registers the app, preventing silent permanent loss of dock pins when the userspace-app fetch races the dock GET.
- Restored two deleted regression tests for checklist item event delivery and agent restart survival
- Fixed blind `ALTER TABLE` migration for `created_by` column: now checks `PRAGMA table_info` first and surfaces non-duplicate ALTER failures instead of swallowing them
- Added round-trip and existing-DB upgrade test coverage for `created_by` persistence on checklist items
- `projects.db` writes can no longer wedge the whole board: the eight stores that share the file (projects, tasks, elements, canvas, doc reviews, notes, lists, list entries) now open their connection in autocommit mode and run every write through one explicit-transaction helper that BEGINs, COMMITs on success and ROLLBACKs on ANY exception — including `asyncio.CancelledError` from a cancelled request. A store that raised or was cancelled between a DML and its `commit()` previously left its connection inside an open transaction forever, and every other store then failed with `sqlite3.OperationalError: database is locked` (task create, `claim_task`, …) until the controller was restarted
- A rollback on any of those stores now logs at ERROR with the store name, so a leaked write shows up in the journal instead of silently locking the database
- `apply_wal_pragmas_async` now sets `PRAGMA busy_timeout = 5000` like its sync twin, so a connection that meets a concurrent short write on the same file waits for it instead of failing the request
- The rollback now also covers the `BEGIN IMMEDIATE` itself. That statement is the one that waits — while another connection holds the write lock it blocks for the whole `busy_timeout` — and the driver has already handed it to the connection's worker thread, so a request cancelled at that boundary opened a transaction after the coroutine that would have closed it was gone: the same wedge, one statement earlier
- **CI**: `scripts/check_store_wiring.py` no longer demands an `app.py` registration for a class that other stores inherit from AND that declares no `SCHEMA` of its own. Such a base exists to be inherited, never to be assigned to `app.state`; a class that owns tables is a store and stays policed however many subclasses it grows, so a new unreachable store keeps failing the gate
- Every `rowcount` check on those stores is now taken inside the transaction that produced it, instead of reading the cursor after the block had closed — the value was correct either way, but nothing in the source said whether it was the pre- or post-commit one
- A handed-off rollback that is itself cancelled now logs at ERROR naming the store, so a connection that may still hold a transaction is visible in the journal rather than silent
- A write that runs inside another write on the same store now joins the open transaction instead of waiting on the per-connection lock its own task already holds. The outermost scope still owns the single commit or rollback, so the nest stays one transaction — without the join, a store method calling another store method would have hung that store until the next restart
- Parking a task now writes the status and clears the stale claimer in ONE transaction: parked is terminal, so a claimer left behind by a failure between two separate writes made the card look held by an agent that could never release it
- An edit refused by the parked guard no longer publishes `task.updated`. The guarded UPDATE matches no row when the task was parked after the pre-read, so the event announced a patch the database never took
- `delete_element(untag=True)` only tolerates a not-yet-created canvas table on the canvas untag. Any other `sqlite3.OperationalError` there now aborts the delete instead of committing an element deletion with canvas rows still pointing at it
- The project-name uniqueness check and the lead-membership check now read inside the transaction that writes. Neither column has a unique index, so outside the transaction two concurrent creates both passed the name check, and a member removed between the check and the write left `lead_member_id` dangling
- The `task.parked` audit row now records the status the card actually came from. Parking accepts a quarantined card, and quarantine keeps the claimer, so inferring the source status from `claimed_by` logged a quarantined card as coming from `claimed`
- A read on one of those stores can no longer come back with another task's uncommitted rows. Each store's reads and writes share one connection, and sqlite shows a connection its own uncommitted changes, so a read issued while a transaction was open was executed between that transaction's statements and returned rows that could then roll back. Reads now queue on the same per-connection lock the transaction holds; a read from the task that owns the transaction still runs straight through, because a write has to see what it has just written
- Events and audit rows now wait for the OUTERMOST transaction to commit. A mutation called from inside another one joins the open transaction rather than nesting, and the join returns without committing, so anything it published on its way out announced a write the enclosing scope could still roll back. Those effects are queued on the outermost transaction and dropped with it on rollback
- A document review transition is validated inside the transaction that writes it. Checked outside, two concurrent transitions both saw `awaiting_review`, both passed, and the second landed a transition that was never legal from the state it actually met
- `update_project` now runs the same case-insensitive name check `create_project` does and answers 409 on a rename onto a name another project already holds. `projects.name` has no unique index, so the rename left two projects sharing one name and `get_project_by_name` returning only one of them
- Archiving a checklist item now enforces `verified = 1 AND reported = 1` in the UPDATE itself and refuses a zero-row result. Validated only before the transaction, an `update_checklist_item` that withdrew either flag in the gap archived an item that no longer satisfied the invariant — and announced it
- The canvas permission PATCH answers 404, not 500, when the member is removed between the flag write and the read-back that follows it
- **CI**: `scripts/check_store_wiring.py` now tracks classes by the module that defines them. Keyed by bare class name, a subclassed base in one module handed its no-`SCHEMA` exemption to an unrelated, unwired class of the same name in another module, and the gate reported clean for a store nothing could reach. It also checks `app.py` wiring before the exemption, so a base that IS wired is logged as wired instead of as an unreachable base
- Board: the lead-only Unquarantine button is now reachable by keyboard (Enter or Space on the focused button activates onUnquarantine instead of bubbling to the card's role="button" handler and opening the task).
- Board: a live `task.quarantined` SSE event now carries `strike_count` and `latest_strike` (producer: `ProjectTaskStore.quarantine_task`), so the badge stops reporting "0 strikes" until reload after a card is quarantined mid-session.
- `bus_stream` now parses `since` from query params manually instead of relying on FastAPI's `float | None = None` annotation, returning project-consistent 400 errors for non-numeric and non-finite values (matching `/api/a2a/bus/messages` behavior). Docstring updated to document `since` as a message `ts` (float), NOT an id, and that unknown query params are rejected 400.
- **Gate integrity token usage**: `_get_token()` is now called as fallback in `check_gate_integrity()` so the workflow's `GITHUB_TOKEN` env is actually used when authorizing API requests.
- **Protected paths expanded**: `docs/doc-gate.toml`, `pyproject.toml`, and `tests/conftest.py` added to `PROTECTED_PREFIXES` so gate rules are enforced for these data files.
- **pull_request_target rationale corrected**: Comments updated to accurately state that `pull_request_target` checkout defaults to the base branch, not the merge ref, while keeping the explicit `ref: ${{ github.base_ref }}` pin.
- **SKILL.md whitespace reverted**: Re-indent of the two `Tests-Skipped-Intentionally` lines reverted from 3-space back to 2-space.
- Wake-budget reporting now passes the agent's bound `project_id` instead of `None`, so per-project consumption is measured against the same state key that enforcement writes.
- Observatory `/api/observatory/wake-budget` now returns rows for agents with `status: "running"` (the only status the heartbeat wakes), instead of silently returning an empty list.
- Removed the unused `mention_cap` / `mention_count` half of the wake-budget module, which had zero production callers and structurally always reported zero mentions.
- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` no longer collapses oversized snapshots to an empty `_truncated` marker: it now drops the largest fields first until the serialized form fits within 32768 bytes, preserving smaller fields and recording the dropped field names in the marker when the marker itself fits within the limit.
- The light-theme compatibility layer now inverts arbitrary-value overlay
  utilities (`bg-white/[0.04]`, `border-white/[0.06]`, and their hover
  variants), not just the plain-fraction form (`bg-white/5`). The shared
  primitives (card, button, tabs) and ~126 app surfaces used the
  arbitrary-value form, which matched no attribute selector and so kept
  additive white overlays that vanished on light backgrounds.
- Auth middleware now distinguishes unknown paths from known paths with the wrong HTTP method when a registry JWT is presented: a live credential on a truly unknown URL returns 404, while a wrong verb on an existing URL falls through to the session gate's 401. This closes the gap where `check_agent_identity` + `_any_route_matches` could be bypassed by a wrong-method request, and ports the `TestRegistryJwtUnknownRouteDispatch` regression suite from #2792 onto dev's mechanism.
- Wake-budget read surfaces now resolve the per-agent/per-project key from the
  agent's held task first, so the charge is not lost the moment the agent claims
  its task and the task leaves the ready-tasks view.
- Define the one-shot lifecycle-reconcile subscriber before `backend_catalog.subscribe()` registers it, so app startup no longer dies with `UnboundLocalError` on a nested-function forward reference (app.py)
- Register that subscriber before `backend_catalog.start()` so the first probe pass cannot fire past an unregistered subscriber (app.py)
- `BackendCatalog.stop()` now sets the first-probe barrier before replacing it, releasing anyone parked in `wait_initial_probe()` instead of stranding them on an Event the cancelled poll task will never set (backend_catalog.py)
- `BackendCatalog.start()` no longer swaps the barrier, which could strand a caller that began awaiting `wait_initial_probe()` before `start()` ran (backend_catalog.py)
- Agent state versions API now parses auto-commit subjects that contain `|` by using a non-printable delimiter instead of the pipe character.
- Reverting to the current HEAD returns 200 `{"status": "noop"}` instead of 404; non-ancestor SHAs return 409, and dirty working trees return 409.
- SHA validation tightened to require at least 7 hex characters.
- SSH key files under `.ssh/` are excluded from agent state history via `.gitignore`.
- Deploy results now surface `versioning: false` and `versioning_error` when the state-repository setup fails.
- The auto-committer is installed as a systemd unit with `Restart=always` so it survives container reboots; nohup remains the fallback.
- The committer script now uses `git diff --name-only` for its change summary, discarding the fragile footer heuristic.
- The committer script checks Git command return codes and logs errors to stderr instead of silently swallowing them.
- The server install no longer lands `litellm-enterprise` (BerriAI, `LicenseRef-Proprietary`) in the shipped venv. `litellm[proxy]` listed it as a plain member, so every install redistributed proprietary code that nothing in taOS imports; the `proxy` extra now inlines litellm 1.94.2's own proxy requirements minus that wheel, caps litellm to the minor the list mirrors (a fresh `pip install -e .[proxy]` does not read `uv.lock`), and the installer uninstalls a copy an earlier install left behind. `yt-dlp` moves from an ad-hoc `pip install` in `install-server.sh` into `pyproject.toml` so it resolves through the lockfile like everything else. `scripts/check_install_licences.py` + `tests/test_install_licences.py` gate both: no blocked licence in the resolved install set, and no installer `pip install` of a package pyproject never declares.
- The litellm cap test now asserts the exact mirrored bound (`>=1.94.2,<1.95`) instead of merely "has some upper bound", which a ceiling as loose as `<2` used to satisfy.
- An unreadable PyPI licence (`--licences`) now fails the gate as its own `unknown-licence` finding instead of passing silently; "we could not tell" is no longer treated as "this is clear".
- The Commons Clause licence check now also catches the hyphenated `Commons-Clause` spelling, not just `commons clause` with a space.
- `install-server.sh`'s `litellm-enterprise` leftover-removal probe now uses `importlib.metadata.distribution()` — the same check `pip uninstall` itself consults — instead of `importlib.util.find_spec()`, and the installer now aborts (rather than warning and continuing) if the package is still present after the uninstall attempt.
- **`.github/` full-tree protection**: `PROTECTED_PREFIXES` now covers the entire `.github/` tree (workflows, composite actions, `.github/scripts/`, Dependabot config, etc.) instead of only `.github/workflows/` and `.github/scripts/` subdirectories. Over-inclusion is safe because the `gate-integrity-allow` label provides an explicit human-set waiver for intentional changes.
- **`per_page=100` pagination on `/files`**: `collect_pr_files` now requests 100 records per page from the GitHub `/pulls/{n}/files` endpoint and `_api_get` already follows `Link: rel="next"` headers until exhausted. A 150-file PR now enumerates all records across two pages.
- **Fail-closed on enumeration mismatch**: the existing `record_count != changed_files` check (EXIT_ERROR) now also catches truncated multi-page listings where `_api_get` stopped following the Link chain.
- **`scripts/check_*.py` nested coverage**: `is_protected` matches `scripts/check_*.py` at any depth under `scripts/` (e.g. `scripts/platform/check_foo.py`), auto-covering future gate checkers including `check_gate_integrity.py` itself.
- install-server.sh now installs Docker Engine + Compose v2 on Debian trixie / Armbian trixie, whose distro apt has neither docker-compose-plugin nor docker-compose-v2 (taOS#2). When both names are missing it falls back to Docker's official apt repo (download.docker.com) after verifying the signing key fingerprint, installing docker-ce + docker-ce-cli + containerd.io + docker-buildx-plugin + docker-compose-plugin. Air-gapped hosts that can't reach download.docker.com still complete the install with a "Store Docker apps will be unavailable" warning.
- Fixed git_log to propagate Git-log failures to the route with RuntimeError, ensuring HTTP 409 when container is unreachable (tinyagentos/agent_git.py:84)
- Fixed git_revert to use a single git operation without leaving the index/dirty, preventing race condition with agent_committer (tinyagentos/agent_git.py:107)
- The external-agent consent approve screen no longer shows a blank project dropdown. The request payload's `project_id` is now carried into the consent notification and rendered in the picker: when the requested project resolves, its NAME and id are shown preselected; when it does not exist or is not visible to the approver, an explicit red "Requested project ... not found" message is shown and the Approve button stays disabled. The Requested vs Granted scope lists are now both rendered, with scopes dropped from the granted set highlighted in red and any scope granted beyond the request highlighted in amber, so a narrowing or widening consent can never happen silently.
- Light-theme white-overlay inversion: added missing `[class~=]` rules for 21 plain-fraction forms (bg-white/8, hover:bg-white/20, divide-white/5, data-[state=unchecked]:bg-white/10, etc.) that were invisible to the #2637 derived guard; widened the coverage test regex to catch both arbitrary-value and plain-fraction gaps going forward.
- Security headers now include `Referrer-Policy: no-referrer` and a restrictive
  `Permissions-Policy` on every response, closing the unauthenticated header gap
  found in the September 2026 security audit (S2-31).
- `X-Taos-Version` is coarsened to `taOS` for unauthenticated callers; the full
  build version is only sent to requests that presented a credential (S2-32).
- GZip middleware is now added inside the CSRF cookie-setting layer so response
  bodies carrying `Set-Cookie` headers are never compressed (BREACH precondition).
- The `/setup` startup-exempt prefix is anchored as `/setup/` so `/setupfoo` is
  no longer matched as a setup path.
- `gui()` now checks for the SPA bundle and exits 503 with a build hint when
  `static/desktop/index.html` is missing, instead of opening a browser to a
  500/404.
- `GET /manifest` without `?app=` now returns a plain 400 instead of leaking
  the FastAPI 422 validation schema (S2-33).
- The uvicorn `Server` banner is suppressed via `server_header=False` (S2-32).
- Implement the missing `tinyagentos.scheduling.reaper` module so `reap_hung_executor_sh` actually exists, the test suite collects, and the changelog claim is no longer false.
- Archive tab no longer wipes active notifications from the shared store; archive rows are held in local component state and merged with existing store entries.
- Active notifications are no longer capped at 10 in the Notifications app; all active items are reachable.
- Dock pin redirect for `notification-archive` no longer carries an unread `section` field.
- Wake-budget read surfaces (`/api/agents/{name}/wake-budget` and `/api/observatory/wake-budget`) now resolve the agent's `project_id` from its first ready task via `project_task_store.list_ready_tasks_for_assignee`, matching the key the heartbeat writes under `record_scheduled_wake`. Previously they read a never-set `agent.project_id` and reported `consumed: 0` against the global bucket while enforcement throttled on the project bucket.
- `restart_orchestrator.py` now writes the pending-restart flag and controller-side resume notes with `atomic_write_text`, so a power loss mid-write cannot produce a torn file.
- `apply_pending_restart_check` clears the pending-restart flag when `current_sha` is empty (unknown revision), preventing a stale flag from surviving an indefinite retry loop.
- Knowledge source resolution (R2-14): `resolve_source_type` now keys platform detection off the URL hostname (exact or a subdomain) via `urlsplit()`, so a platform name in a query string or foreign path (`evil.com/?x=youtu.be/abc`) can no longer spoof a classification; `parse_github_url` rejects non-`github.com` hosts; the GitHub fetcher omits the `Authorization` header when no token is configured (no more `Bearer None`); and 403/429 responses with `Retry-After` or `X-RateLimit-Reset` are honoured with a single retry.
- Zabbly keyring written 0600 breaks every subsequent apt-get update: changed `cp` to `install -m 0644` in scripts/install-server.sh
- Auth middleware now validates credentials first: if a valid token is presented,
  the request falls through to routing and unknown paths return 404 instead of 401.
  If the credential is absent or invalid, 401 is returned uniformly (anti-enumeration
  property preserved for anonymous callers).
- `docs/agent-onboarding.md` no longer references non-existent files (`docs/STATUS.md`, `docs/AGENT_HANDOFF.md`), removes the freshness-cron re-arm instruction, corrects the canonical task list to the `prj-5y722y` board, fixes the A2A bus identity rule, quotes all required CI contexts, corrects Kilo and CodeRabbit review policies, and adds `changelog.d/tsk-<cardid>-<slug>.md` as an equally valid fragment naming convention.
- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` preserves `agent_id` and `session_id` by contract instead of by accident of value size: both drop paths now exclude them while any other field remains, so a resume note carrying a large `agent_id` beside many long-named fields no longer loses the identifiers the note exists to carry. They are still dropped if they alone breach the 32768-byte limit, which the capped snapshot never exceeds.
- A `context_snapshot` that is not an object is bounded too. A framework writes its own resume note, so the snapshot can arrive as a string or a list; an oversized one used to be posted to `/resume` unchanged on both the boot pass and the retry loop, and is now replaced by a bounded `_truncated` marker. Small non-object values are left untouched.
- The `_truncated` marker is no longer lost when the snapshot ends up flush against the limit: room for a bare marker is reserved before the drop loop stops, and the dropped field names are filled in from whatever room is left over.
- Capping a large snapshot is no longer quadratic. The drop loop re-serialized the whole snapshot on every iteration to test the limit; it now tracks a running byte total, which takes a 5000-field snapshot from 63s to 56ms on the boot resume path.
- `schema-column-guard` now resolves `SCHEMA` constants via AST so that docstrings and unrelated triple-quoted strings containing `CREATE TABLE` do not produce false positives.
- `schema-column-guard` now only matches `ALTER TABLE ... ADD COLUMN` inside the string literals of a `_post_init` **method**, read out of the AST; comments and docstrings can no longer silence a column, a `#` inside an SQL literal can no longer swallow one, and a module-level helper named `_post_init` no longer speaks for every store in the file.
- `schema-column-guard` now exits non-zero with a loud warning when the baseline ref is missing, instead of silently treating every column as a violation.
- `schema-column-guard` compares against the PR's own base branch (`--base`, `BASE_REF`, default `origin/dev`), so a `master`-targeted PR is no longer blocked by a ref its checkout never fetched.
- `schema-column-guard` parses the baseline snapshot with AST too, so a `CREATE TABLE` in a docstring on the base branch can no longer invent a baseline column and mask a real violation.
- `schema-column-guard` resolves each `SCHEMA` in its own lexical scope, so two classes that both alias a same-named constant no longer collapse onto the first value; f-strings and `+`-concatenated literals resolve, and a `SCHEMA` it cannot resolve is reported on stderr as unchecked rather than skipped in silence.
- `schema-column-guard` treats a file it cannot read or parse as a hard failure (exit 2) instead of reporting the run clean, and prints accumulated violations before that exit so one CI run shows the full picture.
- `schema-column-guard` decides whether a file exists on the baseline from `git cat-file -e`'s exit status rather than by matching git's stderr wording, which is version-dependent and localized.
- `schema-column-guard` strips SQL `--` and `/* */` comments before splitting a `CREATE TABLE` body. An inline comment runs to the end of its line including the comma that ends the column, so previously every column declared after the first commented one was invisible to the guard - a store documenting its columns inline (the house style) was almost entirely unchecked.
- `schema-column-guard` only diffs tables that already exist on the baseline. A brand-new table is built in full by its own `CREATE TABLE IF NOT EXISTS` on every install, so no ALTER applies; diffing it emitted one violation per column on every new store.
- `schema-column-guard` no longer drops a column whose NAME collides with a SQL word. A reserved word followed by a type is a column declaration, not a clause; `key TEXT` and `text TEXT` are live in eight stores (desktop_settings, todo_store, task_store, job_queue and others) and were invisible to the guard on both sides of the comparison.
- `schema-column-guard` only reads ALTER statements written directly in a `_post_init` body. A never-called helper or a nested class defined inside `_post_init` can no longer silence a column with SQL that never executes.
- `schema-column-guard` tracks quote state while splitting a `CREATE TABLE` body. A bracket inside a string literal (`DEFAULT ')'`) used to move the nesting depth, after which the comma ending that column stopped splitting and every column declared after it vanished from both sides of the diff.
- `schema-column-guard` resolves `SCHEMA` aliases in statement order. A constant reassigned later in the body used to win, so the guard could compare a table definition the runtime never builds; a forward reference now resolves to nothing and is reported, matching the `NameError` it would raise.
- The doc-gate workflow declares `permissions: contents: read`. It only reads its checkout, so it no longer inherits whatever the repository or org default grants `GITHUB_TOKEN`.
- Notification Archive tab now has real tests covering abort-on-unmount and finally-guard defects, replacing a placeholder scratch test; the abort-ref is also cleared in the finally block to make the identity guard robust
- Wake-budget enforcement no longer fails open on a damaged `wake_budget.json`: an absent file is treated as a fresh state, while a present-but-unreadable or unparseable file makes `can_wake` return False (fail closed) and surfaces the corruption in the log instead of silently restoring a full budget fleet-wide.
- `record_scheduled_wake` in the heartbeat loop is now called before the debounce stamp and outside the per-agent `try/except`, so a persistence failure propagates to the sweep-level handler rather than silencing the agent for the cooldown on an uncharged wake.
- `AppConfig.wake_budget` nested mappings (`per_agent`, `per_project`) are deep-copied per instance at both construction sites, preventing cross-instance and `DEFAULT_CONFIG` leakage.
- Restored legacy `notification-archive` dock pins now open the Notifications app Archive tab instead of the default Notifications tab.
- Model downloads no longer hang forever on a stalled connection. The HTTP
  transfer in `tinyagentos/download_manager.py` ran with `timeout=None`, which
  disables the connect, read, write and pool timeouts together: a Wi-Fi drop, a
  NAT table eviction or a CDN edge that stopped sending left the task at
  `status="downloading"` with nothing ever erroring, showing a progress bar
  frozen part-way with no way to tell it apart from a slow link. It now uses
  finite timeouts and retries transport errors and 5xx responses with
  exponential backoff, so a single transient failure from a mirror no longer
  kills a multi-gigabyte transfer. Expect previously invisible stalls to start
  surfacing as errors — that is the fix working.
- Interrupted model downloads resume instead of restarting. Bytes already on
  disk are asked for with a `Range` header, so a 40 GB model that fails at
  39 GB continues from where it stopped; a server that ignores the header and
  answers `200` restarts cleanly rather than appending a second copy.
- A failed model download no longer leaves a corrupt file at the canonical
  path, where every later "is this model installed?" existence check would take
  it for a real weight. Bytes are staged in a `<dest>.part` file and renamed
  onto the destination only after validation passes.
- Finished download tasks are pruned after an hour instead of staying resident
  for the lifetime of the process, so `/api/models/downloads` no longer grows
  without bound. Pending and downloading tasks are never pruned.
- A re-download of an already-installed model no longer deletes the existing
  valid file when the new attempt fails before promoting anything: the
  cleanup on failure now only removes `task.dest` when this attempt actually
  renamed the `.part` stage file onto it.
- _degrade now reads response.buttons, response.images, response.cards and emits one-time notices; chunks on encoded bytes with [part N/M] prefix byte-accounting; total derived from byte-accurate chunking
- `LLMProxy.start()` in `tinyagentos/llm_proxy.py` now exports `TAOS_TRACE_URL`
  derived from the controller's bound port so the `TaosLiteLLMCallback` inside
  the LiteLLM subprocess can POST trace, lifecycle and spend events to the
  correct controller instead of silently dropping them on non-default ports
  (#tsk-j2l2qy).
- `TaosLiteLLMCallback` in `tinyagentos/litellm_callback.py` now falls back to
  `TAOS_PORT` (defaulting to 6969) when `TAOS_TRACE_URL` is unset, so standalone
  callers and custom-port installs still emit traces (#tsk-j2l2qy).
- Container memory limits and container disk usage are read correctly again. taOS writes limits like `512m` and reads them back in incus's `2GiB` form, but the memory parser understood neither and reported **0 MB** for every userspace app container, while the worker-capacity parser raised on the same string. The disk-quota scanner recognised no unit above GiB, so a container whose rootfs passed 1 TiB produced no usage record, no notification and **no quota enforcement** — indistinguishable from an idle container. All five byte-size parsers now share one implementation in `tinyagentos/size_units.py`, covering `512m`, `2GiB`, `1.5TiB`, `100G` and raw byte counts, and the quota scanner logs a warning when it cannot read a usage line instead of skipping the container in silence. Memory figures now follow incus's own units, so a limit written as `2GB` (SI, 2,000,000,000 bytes) reports 1907 MiB rather than being rounded up to 2048; write `2GiB` for the old number.
- The disk-quota scanner reads the layout `incus info` actually prints. incus puts the storage figure on the line *after* the `Disk usage:` header (`root: 1.50TiB`), which the old single-line match never saw: the TiB fix reached no real container, and every container logged an "unparsable disk usage line" warning on every 15-minute scan. The scan is now section-aware, warns only when a disk-usage section really carries no size, and says which of the two faults it hit — no size token in the section, or a size token it could not read.
- Byte sizes are parsed exactly and non-negatively. `-1GiB` used to yield a negative memory limit and a negative disk-usage figure; `inf` and `1e309` raised an uncaught `OverflowError` past every caller's `except ValueError`; and byte counts above 2^53 lost their low bits to a float round-trip. All four are now `ValueError` or exact.
- `taos worker resize-storage --size` hands truncate(1) a byte count instead of the string typed. truncate has no IEC suffixes, so `--size 1.5TiB` — which the parser accepts and the pre-flight check passed — used to fail *after* the worker LXC had already been stopped. The `--size` help text now states the units the parser really takes.
- The 1,455 shipped agent templates ask for `1GiB` of memory rather than `1GB`. With SI and IEC no longer conflated, `1GB` is 1,000,000,000 bytes (953 MiB); the templates have always meant a whole gibibyte, and now say so, so allocations are unchanged from before the parser rewrite.
- The disk-quota scanner no longer runs a second, identical `incus info` read after the first one comes up empty. `_sample_btrfs_qgroup` and the now-removed `_sample_incus_info` parsed the exact same output with the exact same section-aware scan, so the second call could never return a different answer — it only doubled the subprocess cost, every scan, for every container whose pool isn't btrfs-backed.
- `llm_proxy.py` readiness poll now checks `proc.poll()` each iteration and fails fast with the stderr tail when the proxy process exits at startup, instead of blocking for the full 120 s timeout (R2-29).
- Wake-budget reader (`get_agent_wake_budget`, `get_fleet_wake_info`) no longer goes blind when an agent holds no task: consumption is now summed across all of the agent's project keys, so closing all tasks cannot make `consumed` report 0 while the enforcer charged under project keys.
- Enforcer (`can_wake`, `get_next_scheduled_wake`) now applies the daily budget as a per-agent ceiling: `global_default: 2` and `per_agent` overrides limit the total number of scheduled wakes for the agent across all projects, matching TOKEN-DISCIPLINE rule 6. Reader and enforcer now use the same per-agent semantics.
- Route PowerShell VMAF eval diagnostics (source/variant not found, ffmpeg failure) to stderr via `[Console]::Error.WriteLine` instead of `Write-Host`, so the ffmpeg-failure diagnostic no longer pollutes the stdout CSV stream on PowerShell 6+ and breaks the header/data-row contract
- All four proxies (`routes/account_proxy.py`, `routes/service_proxy.py`,
  `routes/userspace_apps.py`, `routes/shortcut_proxy.py`) now strip every cookie
  taOS itself issues before relaying a request upstream, instead of each
  carrying its own hand-written strip list. Between them those four lists named
  two of the five cookies this origin sets, so `csrf_token`, `taos_browser` (an
  httponly session id bound to a `user_id`) and `taos_cs` were relayed to
  taos.my, to container app backends and to shortcut targets. `csrf_token` is
  deliberately `httponly=False` so the SPA can read it, which makes it a
  readable origin-wide secret whose only job is proving same-origin — relaying
  it handed an upstream exactly what satisfies `verify_csrf`. The deny-list is
  now a single shared `TAOS_ISSUED_COOKIES` frozenset in
  `tinyagentos/issued_cookies.py`, and `tests/test_proxy_cookie_isolation.py`
  asserts both that all four proxies share it by identity and that it covers
  every `set_cookie` call in the package (#tsk-jqcvpc).
- Sparkle appcast snippets now include the matching changelog section for the current version, escape CDATA terminators, and include sparkle:shortVersionString.
- LoRa 237-byte wire budget now chunks on UTF-8 codepoint boundaries instead of slicing bytes, fixing corruption and over-budget frames for non-ASCII (CJK, emoji) messages; the Meshtastic connector guard now raises instead of narrate-truncating-and-shipping oversize frames, and the `meshtastic` platform is reachable via `POST /api/channel-hub/connect`.
- Mobile home default grid now applies the same tier rule as the desktop launcher: tier 1 and tier 2 apps surface on the default grid, while tier 3 apps (providers, mcp, channels, notification-archive) are excluded and remain searchable. Extracted a shared `isDefaultSurfaceApp()` helper in the app registry so the mobile home grid and launcher can no longer drift into different tier predicates (#2517).
- Red-proven test added: `mobile-home-store` default grid is asserted to exclude the real tier-3 registry id `providers` (and the other tier-3 apps); reverting the predicate to `tier !== 4` turns the test red, restoring `isDefaultSurfaceApp` turns it green.
- The light-theme compatibility layer now inverts `bg-white/8` to `rgba(0, 0, 0, 0.05)`
  instead of `0.08`, restoring the strictly increasing scale `/5 (0.04) < /8 (0.05)
  < /10 (0.06) < /15 (0.08) < /20 (0.10)`. Previously `/8` collided with `/15` and
  exceeded `/10`, so surfaces using `bg-white/8` for a subtler affordance than
  `bg-white/15` rendered identically, and darker than `bg-white/10`. The `focus:`
  mirror follows the base value; the `hover:` mirror uses its own ramp
  (`/5 (0.05) < /8 (0.06) < /10 (0.07) < /15 (0.08) < /20 (0.10)`), so `hover:bg-white/8`
  is `0.06` rather than tying with `hover:bg-white/5`.
- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` drops the largest fields first until the serialized form fits within 32768 bytes, preserving smaller fields and recording the dropped field names in a `_truncated` marker when that marker itself fits within the limit.
- S2-24: Prevent workers from claiming unlimited leases on fabricated resources. The `_worker_for_resource` method now validates that the resource part of a resource_id matches one of the worker's registered backends before accepting the lease claim. Workers are also limited to a maximum of 10 concurrent leases each (configurable via `_max_leases_per_worker`).
- Fixed per-user broadcast read/archived state so `list()` and `unread_count()` correctly exclude broadcasts archived by a specific user, and the `unread_count` query no longer references an undefined table alias.
- Registry lookup failures in `GET /api/observatory/fleet` are now logged (they were already swallowed, so the fleet view kept rendering).
- Installer no longer gives up on the controller after 120 s on first boot. The wait is split into a 60 s port-open phase (using `/api/health`) and a 240 s readiness phase (using `/api/cluster/workers`), with an error message that names first-boot init when the port is open but the app is still starting. This prevents the false "install failed" report on slow first boots where a re-run would succeed (taOS#2).
- Merge attribution reconciliation (`scripts/check_merge_attribution.py`) compared the `--cutoff` against each PR's `mergedAt` as strings. A cutoff given as a git SHA is resolved with `git log --format=%cI`, which carries the committer's local UTC offset, while `mergedAt` from the GitHub API is always UTC with a trailing `Z`; comparing the two lexicographically mis-ordered them, so any cutoff SHA committed east of UTC silently dropped in-scope merges out of the audit and never reported their missing audit lines. Both sides are now normalised to UTC and compared as instants. A `--cutoff` that resolves to neither a git ref nor an ISO-8601 timestamp is now an infrastructure error instead of an empty scope, and a PR with a missing or unparseable `mergedAt` is kept in scope rather than dropped.
- `scripts/gate_merge.sh` now builds its audit entry with a real JSON encoder (`jq`, falling back to `python3`) instead of interpolating shell variables into a hand-written string, so a quote or backslash in the actor, repo or merged-by field can no longer emit an audit line the checker cannot parse.
- A legacy `notification-archive` dock pin now survives a session restore and opens the Notifications Archive tab. Restoring the dock rewrote the pin to `notifications`, which threw away the Archive destination before the dock, the Ctrl+1–9 shortcuts or the app could read it, and the dock auto-save then wrote the stripped id back to the server, so one reload lost the pin for good.
- Hardened the macOS dock icon list: matching a pin's running state no longer trusts a non-null assertion on a positionally-indexed lookup, so a future refactor that lets the pinned-id and app-id arrays diverge fails safe instead of crashing.
- `TAOS_TRACE_URL` now targets the taOS controller port (resolved via `TAOS_PORT` env var, `config.server['port']`, or default 6969) rather than the LiteLLM proxy port, so trace POSTs from the LiteLLM callback always reach `/api/trace` on the controller regardless of the proxy's bound port.
- the `.claim` sidecar now records `<pid> <boot_id>` and is reclaimed only when the owner is proven dead; a live-but-slow writer is never preempted
- `OMPAdapter` now forces `command=["omp", "acp"]` when given an `ACPConfig`, instead of silently driving whatever binary the config names. `OMPConfig` subclasses `ACPConfig` with the OMP command as the default so the documented usage (`OMPConfig(session_key="...")`) works without requiring `command` to be supplied.
- **bot-review-gate false-red on automatic CodeRabbit clean reviews**: `scripts/check_bot_review.py` now recognises a completed CodeRabbit review that found zero findings using the three-marker rule (no-actionable + Run ID + Files selected N>=1) instead of the prior quota-line discriminator. The quota line appears only on manually-triggered reviews (`@coderabbitai full review`), so requiring it left every automatic PR-open review red. The PASS message prints the Run ID and N.
- **bot-review-gate could not be spoofed by a rate-limit stub**: the zero-finding predicate now rejects a rate-limit stub outright, ahead of the three markers. CodeRabbit's real rate-limit comment already carries the auto-summary marker, a Run ID and a non-zero selected-file list, so only the absence of one phrase separated it from a genuine clean review.
- A browser holding an **expired** `taos_session` cookie could no longer sign in: every sign-in surface answered `403 CSRF token missing`. `verify_csrf` is attached router-wide, and its exemption tested for the *absence* of a session cookie as a proxy for "not signed in" — a proxy that inverts exactly when a stale cookie is present, which is the one moment a user needs to sign in again. The server-rendered form cannot satisfy the double-submit check either, having no JavaScript to attach the header, so retrying could never clear it. On a keyboard-less kiosk this was unrecoverable. Credential-establishing routes (`/auth/login`, `/auth/pin-login`, `/auth/setup`, `/auth/complete`, `/setup/complete`) are now exempt **by path**; everything acting on an already-valid session, including `/auth/logout`, `/auth/pin` and the `/auth/users/*` mutations, still requires the token.
- The PIN keypad reported a **correct** PIN as "Incorrect PIN.". It rendered `body.error`, but a rejection raised by a dependency rather than returned by the handler serialises as `{"detail": ...}`, so it fell through to the literal. The panel now reads `detail` as well as `error`, and the fallback text no longer blames the PIN for a failure that had nothing to do with it.
- `/api/import/upload` and `/api/import/embed` now place files under `<data_dir>/imports/uploads` instead of a predictable `/tmp` path, and refuse requests when the upload directory is a symlink, not a directory, or not owned by the service user.
- Remove unused `project_id` binding in `tests/projects/test_strike_wiring.py:142` (replace with `_` per RUF059)
- Make threshold parking indivisible from release: serialize strike recording and `park_task` in `release_task` so a concurrent claim between release and strike recording does not leave the task `parked` with a stale `claimed_by` value
- Prevent transitions out of `parked`: reject generic status transitions from `parked` in `update_task`, and add `parked` to the `NOT IN` list in `close_task` and `reopen_task` to prevent a parked task from being closed/reopened into the ready pool
- `scripts/check_bot_review.py` now anchors its "Bot review gate" check run
  verdict to the PR head SHA so a later `SUCCESS` supersedes an earlier
  `FAILURE` on the same SHA (#2493). Previously the gate published a fresh
  check run for every workflow run but never reconciled the old one, so a
  self-heal left a stale `FAILURE` coexisting with the new `SUCCESS` and
  `mergeStateStatus` stayed `UNSTABLE` forever. `check_run_verdict` reads the
  latest *completed* bot-review-gate run as authoritative (in-progress runs
  are skipped so a half-written verdict never clears a stale red), and
  `reconcile_head_sha_check_run` PATCHes stale runs to the new conclusion and
  POSTs a fresh run when absent. The gate also splits its single
  `CODERABBIT_SCAFFOLDING_RE` into per-fragment `is_coderabbit_acknowledgement`
  and `is_coderabbit_auto_summary` detectors so a regression in one cannot be
  masked by the other, and the main job now passes `--head-sha` with
  `checks: write` so every relevant PR event re-anchors the verdict.
- Added regression coverage for `_cap_context_snapshot()` on many-small-fields snapshots (long field names and short values, and many short-named fields) staying within the 32768-byte limit. The marker-overhead accounting fix this card targeted was independently landed with a more thorough byte-precise budget in tsk-kkxn6f's `_build_truncated_marker()`; this fold pass kept that implementation and this card's added tests.
- VNC password: replaced hardcoded 'testpass' with a per-start random password generated via `secrets.token_urlsafe`, and kept it off every command line (CWE-214) -- it is written to a mode-600 host temp file, pushed into the container with `push_file`, read by `vncpasswd -f` from that file, and unlinked on both host and container in a `finally`, so it never appears on the host `incus exec` argv or the in-container `bash -c` argv where any local principal could read it from `/proc/<pid>/cmdline`
- Desktop status probe: a non-zero result from the status probe (exec timeout, missing container, unreachable host) means the probe never ran, so it now leaves the tracked state alone and returns 500 instead of recording `stopped` -- which would have let the next `start` bind a second Xvfb on `:1` and a second x11vnc on 5900
- Owner access: the four `/api/agents/{agent_name}/desktop/*` handlers now resolve the agent through the registry and enforce owner-or-admin (403 otherwise) before deriving a container name, touching state, or returning the VNC password; a name with no registry row is administrator-only, and a name that is not a valid container slug is rejected with 400
- Desktop start readiness: the start probe watches the PIDs it launched and connects to port 5900 rather than matching `pgrep -f x11vnc`, which also matched the wrapper shell, and reports `state = "running"` only once the VNC server accepts a connection
- Install retry: installation completion is tracked on its own flag, so a transient apt failure no longer permanently skips installation on every later request; `start` is rejected with 409 while installation has not completed, and an install call on an already-installed desktop clears an error left behind by a later start or stop instead of answering 200 with that error
- Lifecycle serialization: install, start, stop and status hold a per-agent lock across their state updates and container commands, so concurrent installs cannot both run apt and a stop cannot complete underneath an in-flight start
- Stop failures: a non-zero result from the stop command records `error` and returns 500 instead of reporting `stopped` while desktop processes are still running
- Docs: fixed literal `\n` sequences in `docs/routes.d/14-agent-desktop.md` and `docs/routes.md` that collapsed the agent-desktop section onto one physical line
- VNC secret cleanup: a start whose in-container secret file could not be removed now fails closed with 500 and no `vnc_password` in the response, instead of returning the password while a copy of it might still be sitting in the container
- Start exceptions: an exception raised while writing, pushing or reading the VNC secret is now caught, records `state = "error"` with `last_error`, and re-raises, instead of leaving the tracked state at `"starting"` forever (which 409'd every later start and could not be reset by `install`)
- Status probe response: a failed probe now reports `running: null` (unknown) instead of `running: false`, since the latter contradicted a tracked `state` of `"running"` for any client reading either field
- Ensure temporary file cleanup always runs in `_atomic_write` function in `tinyagentos/routes/observatory.py`. Added `finally` block to delete temporary file even when `json.dumps()` raises an exception, preventing orphaned temporary files.
- **Agent-as-a-Model conversation history ordering**: `_run_agent_turn` in
  `tinyagentos/routes/agent_model_api.py` emitted prior conversation segments
  out of source order because user turns were deferred until the next user
  message arrived, while system/assistant messages were appended immediately.
  This inverted every user/assistant pair (`[u1, a1, u2]` became `a1, u1, u2`,
  and the pattern compounded as history grew). The fix appends each message in
  source order in a single pass and identifies the last user message separately
  (via index) so it remains the final prompt without reordering the transcript
  (#2500, fix-forward tsk-o43fol).
- Fixed SpanStoreRegistry to use bounded LRU eviction
- Made undeploy_agent actually remove trace directory when delete_state=True
- Added busy_timeout pragma to browser_sessions.py
- The deleted-symbols CI guard (`scripts/check_deleted_symbols.py`) no longer leaves synthetic parent packages and reloaded modules in `sys.modules`, so its pass/fail verdict no longer depends on which signal symbol it resolved first. It also now records bare `from . import submodule` re-exports, fixes the package-fallback suffix replacement, and cleans up its merge-tree temp dir.
- Bare or empty `wake_budget:` YAML keys (parsed as `None`) are now tolerated as defaults (the documented 2-wake global default applies) instead of raising `wake_budget must be a mapping`. The b4vs65 changelog claimed this shipped but the actual code change did not land; load_config now treats a `None` `wake_budget` the same as an absent one, and `validate_config` no longer rejects `None`. Live regression risk for existing configs whose `wake_budget:` line is empty.
- Damaged wake_budget.json rows in `get_fleet_wake_info` now carry an explicit `state: "damaged"` marker. A `consumed:0/remaining:0` row from a failed state read was previously indistinguishable from a genuinely exhausted agent; the marker names the failed read so the fleet UI can tell the two apart.
- `validate_config` now rejects float values for `per_agent` and `per_project` budgets (the strict `isinstance(val, int)` check). Previously these sections silently accepted floats via `int(val)`, truncating `0.9` to `0` and disabling scheduled wakes fleet-wide for the affected agent/project. (Coverage test added; the strict check was already in place for `global_default`.)
- Fleet wake-info now preserves the first successful read when the second read fails (b4vs65 claim made concrete), and degrades both branches with the `state: "damaged"` marker.
- Auth middleware `_any_route_matches` now renders FastAPI `:path` converter parameters as `.+` instead of `[^/]+`, so registry JWTs on `{name:path}` routes with slash-bearing values return 401 (not 404) when unauthorized.
- Pinned `notification-archive` dock shortcuts now reopen the Notifications app on its Archive tab: `APP_REDIRECTS` carries an optional `section`, threaded through `resolvePinnedRedirect` / `getPinnedRedirectByAppId` and passed to `openWindow` by both the dock-click and keyboard-shortcut paths.
- Tier-3 registry apps (providers, mcp, channels, notification-archive) are now discoverable via the desktop search palette while remaining hidden from the launcher grids and mobile home default surface. Added `getSearchableApps` to the app registry as the search-source path and wired `SearchPalette` to it; `getLaunchableApps` and `isDefaultSurfaceApp` remain unchanged for the launcher/default-surface contract.
- Corrected the #2670 changelog fragment and the `isDefaultSurfaceApp` docstring: tier-3 apps are searchable, not discoverable via the Store.
- `GpuArbiter._drain_queue` now removes the dropped task from `_queued_entries` when the queue-full drop branch fires, preventing phantom task_ids from persisting in `queue_snapshot()` and `queue_position()` forever (#tsk-ord3e5).
- The A2A bus SSE stream proxy (`/api/a2a/bus/stream`) now rejects non-finite `since` cursor values (nan, inf, -inf) with a 400 error instead of forwarding them to the bus, matching the validation already present on the sibling messages endpoint. It also rejects unknown query parameters with a 400 instead of silently ignoring them, preventing the same incremental-read confusion that was fixed on `/api/a2a/bus/messages`.
- Fixed PowerShell VMAF eval harness to emit `ERROR` for both `vmaf_mean` and `saving_pct` in the no-score branch, matching the bash harness; injected controlled fake ffmpeg into all three PS1 tests via PATH to make them deterministic
- Knowledge fetches (article ingest, monitor re-fetch, Library web processor) now
  stream responses through a shared `stream_text_response` helper that rejects
  non-text content-types and caps the buffered body at 10 MB, preventing a
  malicious or misconfigured URL from exhausting host memory with a multi-GB
   response.
- Fixed trace_store.list() connection leak by opening read-only connections for listing and closing them after use instead of caching them. This prevents opening a connection and thread per bucket for every bucket touched during list operations.
- `_run` in `update_runner.py` now accepts a `timeout` parameter and kills the child process on expiry, raising `asyncio.TimeoutError` instead of hanging indefinitely.
- `_run` raises `RuntimeError` on non-zero subprocess exit codes instead of silently returning the failure; callers in `switch_to_branch` catch the exception and return `ok=False` to preserve the existing graceful-error API.
- Removed dead `update_to_master` function (-132 LOC), which ran `git reset --hard` and was no longer imported by any production code.
- Reddit comment fetcher (`knowledge_fetchers/reddit.py`) now walks comment
  trees iteratively with depth (2 000) and count (10 000) caps, eliminating
  `RecursionError` on pathologically deep trees. The `edited` field is also
  corrected: `edited=True` (Reddit's legacy boolean) is mapped to `None`
  instead of being coerced to `1.0` (the 1970 epoch), while genuine numeric
  timestamps and `False` are preserved.
- Ingest queue view now shows a distinct "Queue unavailable" state when the jobs endpoint is unreachable, instead of silently rendering the idle "No active or failed jobs" message.
- `docs/agent-onboarding.md` freshness-cron section rewritten to state fleet HOLD (crons stopped, no re‑arm, manual sweep)
- identity rule reordered: `@taOS` is the PROJECT identity; every post uses the current seat's registry identity
- canonical task list rewritten: project board `prj-5y722y` is canonical store, GitHub issues are auxiliary
- `_extract_doc_paths` filter removed so root-level `.md` references are extracted and validated
- **Agent-as-a-Model multi-turn context**: `POST /v1/chat/completions` now forwards the full conversation history (not just the last user message) to the agent turn driver, so multi-turn conversations are no longer silently dropped on each request (qodo bug 2).
- The deleted-symbols gate no longer crashes or mis-resolves on a `.py -> symlink`
  typechange. `_get_symbols_at_ref` now skips symlink/hardlink tar members (a symlink is
  not a real source file, and `tarfile.extractfile` raises `KeyError` on a dangling
  symlink target on Python 3.12+), and `_resolve_symbol` resolves a symlinked module to
  its real target while pinning the result inside the extracted merge tree — a symlink
  that escapes the tree (absolute target or `..`) is treated as not importable instead of
  re-entering the working tree and crashing or mis-reporting.
- `test_guard_detects_stale_sniffio_namespace` now builds the stale-package condition using a real regular package directory (with `__init__.py`) on `sys.path` instead of an empty namespace directory. An empty directory is inert under PEP 420 and can never shadow an installed regular package, so the guard never saw the half-present module and the test passed unconditionally. The regular-package form actually shadows the install, so the guard has something genuine to detect.
- Use `container name` not `container id` in provisioning P2 release note (changelog.d/tsk-5drlbj-agent-container-provisioning-p2.md)
- Harden provisioning inputs: treat optional config as absent when `container_provisioning` attribute is missing; bound canonical-ID component so container name stays within 63-char limit
 - Destroy underlying incus container when `set_env` fails during provisioning, preventing leaked containers on terminal failed requests
- Generated LiteLLM configuration, backend keys and the callback/auth shim files now live under `<data_dir>/litellm/` (dir mode 0700, files 0600 via `atomic_write_text`) instead of the world-shared `/tmp/taos-litellm`, closing the S2-10 local-read / code-execution vector. The per-install master key already lived at `<data_dir>/.litellm_master_key` (0600, created with `O_EXCL`) and is unchanged by this PR. Added `PrivateTmp=yes` to the systemd unit template.
- `write_config()` now raises instead of continuing when it cannot chmod the config directory to 0700, so a generated config or shim is never written into a directory that failed hardening.
- The LiteLLM stderr log is rotated to `litellm.stderr.log.1` (0600) on every start and a fresh 0600 inode opened via `O_EXCL`, preventing readers holding stale descriptors from observing new output and ensuring pre-fix logs (0644) are never re-used.
- The parent's file handle for the LiteLLM stderr log is closed right after the subprocess starts (and on the failed-start path), instead of leaking one descriptor per proxy start.
- The consent approve surface no longer keeps its own copy of the "needs a project" scope list. `GET /api/agents/scope-vocabulary` now publishes the server's grantable scopes and the subset that must be bound to a project, and `ConsentActions` renders the project picker from that response, so a scope added server-side can no longer silently reintroduce the unfixable 400 on Approve.
- If that vocabulary cannot be loaded, Allow is disabled and the failure is shown, instead of falling back to a stale local list and approving with the wrong shape.
- pre-commit hook no longer blocks on diff-gate failures, letting the commit-msg
  hook be the enforcement point so a valid Docs-Reviewed trailer is actually
  reachable locally. The same advisory split applies to invariants.
- Knowledge search (R2-7): `update_item` now deletes the old FTS5 row before
  re-inserting, instead of `INSERT OR REPLACE` which appends a duplicate row
  on a standalone FTS5 table, so re-indexing an item no longer leaves stale
  text searchable.
- A truncated or corrupt `.taos-rollback` no longer loses the recovery route. A half-written record used to abort the script with a bash syntax error, and a record with an empty or malformed `prev_sha` dead-ended on "cannot resolve"; both now fall through to the newest `taos-pre-update-*` recovery tag, which is the whole point of having one. A branch name git would refuse (`feat/..evil`, a trailing `.lock`, a leading `.`) used to abort the rollback outright when both the plain and the `--force` checkout failed; it now costs only the branch, and the recorded commit is still restored.
- `tinyagentos/rollback.py` applies the same object-name and ref-name rules when it reads a record, and refuses to write a target that is not a full object name, so both ends agree on what counts as usable — including whitespace: Python's `$` also matches before a final newline, so a `<40 hex>\n` value used to be writable while `scripts/rollback.sh` refused it, silently leaving the install with a rollback target that did not work.
- `scripts/rollback.sh` strips a trailing `\r` before parsing a `key='value'` line. A CRLF-formatted record (e.g. written or edited on Windows) left the `\r` glued to the end of the value, so the shell's quote-stripping never matched, `sha_safe` rejected the quoted value, and the script fell back to the recovery tag while `tinyagentos/rollback.py` (whose `splitlines()` already normalizes CRLF) reported the real recorded target — the two readers disagreed about an otherwise valid record.
- Notification prefs load and toggle-save errors are now announced to screen-reader users via `role="alert"` live regions.
- ThemesPanel now correctly announces HTTP errors via screen readers (e.g., 500 status codes) instead of silently converting them to an empty theme list. The error is properly caught and displayed in the alert region.
- UpdatesPanel now clears stale error messages when a successful update check occurs, preventing assistive tech from announcing outdated errors. The alert region is properly cleared on successful requests.
- Checklist item create and archive now refuse a task that no longer exists
  instead of publishing their broker event under a topic no project subscriber
  listens to. `task_checklist_items.task_id` declares a foreign key but the
  task store never enables `PRAGMA foreign_keys = ON`, so a checklist item can
  outlive its task; the previous fallback resolved the publish topic to an
  empty string and the `checklist.item.created` / `checklist.item.archived`
  event was silently lost. Both paths now resolve the parent task before they
  mutate, so a refusal leaves no orphan row and no half-applied archive.
- Wake-budget enforcement test now closes the debounced task and creates a fresh ready task before the third tick, so the tick actually reaches `can_wake` and verifies that budget exhaustion blocks further wakes instead of silently skipping at debounce.
- `_read_state` now validates the nested `daily` shape (`daily` and each per-key value must be JSON objects), so a well-formed file with a wrong nested shape degrades the fleet row as `damaged` instead of raising `AttributeError` out of `get_fleet_wake_info`.
- kiosk-setup.sh: refreshes the apt index unconditionally, installs and enables
  `seatd` when available, and adds the kiosk user to the `seat` group even when
  `seatd` was already installed; failures enabling `seatd`, creating the `seat`
  group, or updating group membership abort setup instead of degrading silently
- taos-kiosk.service: `Wants=tinyagentos.service seatd.service` is now one valid
  assignment (the old `Wants=… Wants=…` line dropped the seatd dependency)
- docs/kiosk-setup.md: new page covering kiosk setup
- The consent picker now renders the project picker for all 9 server project scopes, not just the 3 previously listed, so approving requests for `files_read`, `files_write`, `project_lists`, `project_notes`, `project_tasks_create`, or `project_tasks_update` no longer 400s.
- When the requested project does not resolve, picking a visible project or creating a new one now clears the not-found flag and re-enables the Allow button; the New button is also no longer blocked while the not-found message is shown.
- taOS Pocket (`creations/taos-pocket/index.html`) made the Notifications and Decision cards reachable via bottombar tabs (and Left/Right keys), forwarded request `options` through `api()` so the decision accept call goes out as a proper `POST` with a JSON body, and only clears `state.decision` after the accept call resolves successfully so the user can retry on failure.
- Restore `.gitignore` entry for `docs/AGENT_HANDOFF.md`, publish public guide at `docs/agent-onboarding.md`, correct CodeRabbit claim in local playbook, and fold four CodeRabbit contradictions (bus port, URL shape, Kilo output examination, duplicate checklists) in tsk-sy4626
- Search palette no longer drops installed optional apps (coding-studio, design-studio, music-studio, reddit, youtube-library, etc.) from results. The #2680 `getSearchableApps` predicate excluded every installed optional app because `isDefaultSurfaceApp` is false for optional manifests and none are tier 3; restored the `getLaunchableApps` set UNION tier-3 selection so installed optionals remain both searchable and launchable.
- Launcher tier filtering in `getLaunchableApps` now includes installed tier-5 optional apps (e.g., "coding-studio", "design-studio") when they are installed via the Store's optional-install flow. Previously these tier-5 apps were unconditionally excluded even when installed.
 - Red-proven test added to verify the fix: tier-5 optional apps now appear in launcher listings when installed, and are excluded when not installed.
- The fix ensures that the documented contract in "Re-fetch the installed set so the card flips to Open and the launcher surfaces the studio at once" is properly honored, allowing users who install tier-5 studios from the Store to immediately see them in the launcher.
- The deleted-symbols gate now resolves each signal symbol against the merge result before reporting it. Symbols that remain importable at their public path (for example, a module file deleted but shadowed by a same-named package, or a definition moved) are no longer falsely reported as deleted, while genuinely missing symbols and dropped re-exports still fire.
- Fix-forward #2816: restore linkwarden DATABASE_URL (false SQLite premise) and chmod 0600 the generated docker-compose.yml that now carries the real secret
- `gate-integrity.yml`: Resolve the PR base ref via `gh api` instead of relying on `github.base_ref`, so the checkout targets the PR's actual base branch rather than the default branch. (The `workflow_dispatch` trigger this step originally accompanied was removed before reaching dev; the resolution now guards the `pull_request_target` path.)
- Fixed empty-VMAF branch in bash harness: moved `saving_pct` computation before the `vmaf_mean` null check, emit `ERROR` for both `vmaf_mean` and `saving_pct` in the error branch
- Installer wait loops now cap curl probe time and the follow-up sleep by the remaining phase deadline, preventing a stuck probe from pushing the port-open or readiness phase past `_PORT_WAIT` / `_READY_WAIT`.
- The adversarial-verify stage in `tinyagentos/code_analyzer.py` now tracks inline and multi-line block comments using lexical state instead of line-prefix heuristics.
- Hardcoded-secret findings are no longer suppressed by generic string-literal filtering; only the known-exception allowlist applies.
- Network-exfil adversarial verification inspects the actual detector match span rather than the first trigger token on the line, preventing inert earlier tokens from suppressing real later findings.
- String-literal classification now uses a proper lexical scanner that handles escaped quotes and backtick template literals, eliminating false positives from mixed-quote lines.
- The block-comment mask is now a start-of-line state resolved forward to the finding's own match position, so real code after a same-line `*/` (and a dangerous call before a trailing `/* ... */`) is no longer silently dropped. Comment markers inside string literals or after `//` no longer open a block comment for the lines that follow.
- Detector match spans starting at column 0 are honoured instead of being treated as "no span", so a finding at the very start of a line is classified from its own span rather than from an unrelated trigger token later on the line.
- The known-example allowlist for hardcoded secrets is scoped to the finding's match span, so a real key sharing a line with a documented example value is still reported; the secret detector now reports every match on a line rather than only the first.
- The block-comment mask is built once per file instead of once per finding, removing the O(findings x lines) rescan from the app install/publish path.
- Auth middleware: a non-device Bearer on a route that is NOT on the closed
  allowlist and matches no registered route now has its registry JWT
  validated, and a valid token returns 404 instead of 401. The allowlist is
  still checked first and stays closed (no skeleton key); known
  non-allowlisted routes still return 401. Anti-enumeration for absent or
  invalid credentials is unchanged.
- **Bot-review gate fake-green on CodeRabbit acknowledgements**: `scripts/check_bot_review.py` treated CodeRabbit's auto-generated acknowledgement replies (posted when a `@coderabbitai full review` trigger is accepted but no review follows) and the auto-summary comment as real review items, reporting `PASS` on PRs CodeRabbit never reviewed (e.g. #2482 exited 0 on three stub items, zero real reviews). `is_real_item()` now rejects these via the HTML comment markers (`<!-- CodeRabbit review command invocation: ... -->` and `<!-- This is an auto-generated comment: summarize by coderabbit.ai -->`), and `classify()` routes acknowledgement/summary-only output to `FAIL` (exit 1). A genuine review alongside acknowledgements still passes (tsk-u7ho5n).
- **Stub bodies outrank review state, and the red message names every stub kind**: `is_real_item()`'s stub checks deliberately run before the `APPROVED`/`CHANGES_REQUESTED` guard — a review whose own body is a rate-limit stub or scaffolding is the fake-green shape the gate exists to catch, so it fails closed; the docstring now states that ordering and tests pin it (with an empty-bodied decisive review as the control that goes red if the state guard is dropped). `classify()` now lists both stub kinds when a PR carries both, instead of reporting only whichever branch matched first (tsk-u7ho5n).
- The server-rendered sign-in (`/auth/login`) and first-run setup (`/auth/setup`)
  pages now draw the taOS brand mark as an inline SVG instead of a bare Unicode
  glyph (`⌗` on login, `✦` on setup). Those code points are host-font-dependent
  and rendered as a missing-glyph box (TOFU) on machines whose installed fonts
  lack coverage, while these pages are deliberately JS-free and CDN-free so they
  work on any device. The SVG uses `currentColor` and scales to its `.icon`
  container, so no external asset or webfont fetch is required (#2525).
- SHA-256 verification in `DownloadManager._validate_download` and `TorrentDownloader.download` now streams files through `hashlib.file_digest` instead of loading the entire model file into RAM with `read_bytes()`. This prevents multi-GB model downloads from exhausting host memory on 4 GB devices.
- `scripts/check_bot_review.py` now correctly reads check_runs from ALL GitHub API pages instead of only page 1. This fixes the bug where stale bot-review-gate failures on pages beyond the first 30 check runs were invisible to the checker. The `list_check_runs` function now aggregates check_runs from all pages using `[r for page in data for r in page.get("check_runs", [])]`.
- POST `/api/projects/{project_id}/tasks/{task_id}/comments` now rejects an
  empty or whitespace-only `body` with 422 before storing, preventing
  board-noise comments from CLI misuse.
- Q2-1 security hygiene sweep: `litellm_auth` and `routes/agents.py` now compare secrets with `secrets.compare_digest` instead of `==`; `OpenCodeServer.write_config` and its serve.log creation use `atomic_write_text(mode=0o600)` / `os.open(..., 0o600)` instead of `write_text` + `chmod`; `TorrentDownloader._params_from_torrent_url` is async and runs `httpx.get` in `asyncio.to_thread` so it no longer blocks the event loop; `KnowledgeStore.update_item` validates column names against a frozenset allowlist and raises on unknown; the `search_fts` LIKE fallback now escapes `%` and `_` with an `ESCAPE` clause.
- The agent heartbeat no longer silently swallows a missing `data_dir`: the wake-budget guard now lives at tick entry, so a missing `data_dir` fails loudly at the tick/sweep level instead of being caught by the per-agent `except` and leaving every agent silently unwakeable.
- Capped installer wait probes by the remaining phase time (`curl --max-time "$_curl_timeout"`), so no probe can run past its phase deadline: the first probe may get the full budget (60 s port-open, 240 s ready) and every later probe only the time still left in the phase
- Increased `_PORT_WAIT` from 30 to 60 seconds, matching the documented 55–65 s cold-boot bind time on Pi 5 / Orange Pi 5 (no margin added; the new value sits inside that range)
- Added elapsed-time deadline tracking to both the port-open and ready wait loops, ensuring configured phase limits are enforced as wall-clock time rather than just attempt counts
- Desktop rebuild provenance early-return now requires a clean desktop working tree and a present bundle (`static/desktop/index.html`), so local edits, untracked build inputs, or a missing bundle never get served from a stale-but-matching marker. Failures to record the provenance marker after a successful local build are now logged so operators can spot a regression that would re-introduce the original false-positive rebuild.
- The Python mtime staleness check now walks the whole `desktop/` subtree for dependency/build-tool config (pruning `node_modules/`), matching `scripts/rebuild-desktop.sh`'s `find`. It previously checked only direct children of `desktop/`, so a nested `package.json`/`tsconfig*.json`/`vite.config.*` newer than the bundle was invisible to the in-app rebuild path while the systemd path would still have caught it.
- The rebuild staleness check no longer runs `git status` on `desktop/` twice when a marker with a matching SHA turns out to sit on a dirty tree: the provenance check and the dirty-build-input check now share one result for that call instead of each spending their own subprocess on the identical question.
- `scripts/rebuild-desktop.sh` now treats a failing `git status` (no git, broken `.git`, unreadable index, extracted tarball) as a dirty desktop tree instead of a clean one, matching the Python gate — a malformed deployment can no longer serve a stale bundle from the systemd path while the in-app path rebuilds. The post-build provenance marker write also captures stderr in a per-invocation `mktemp` file, so concurrent rebuilds cannot delete each other's error text and swallow a real write failure.
- A local desktop build from a dirty `desktop/` tree no longer records a `HEAD:desktop` provenance marker the bundle was not built from; the marker is invalidated instead, so reverting the edits cannot re-arm a matching marker in front of a bundle built from edited source. A marker that cannot be written (unwritable `static/`) no longer reports a successful rebuild as failed, and the working-tree check uses `--untracked-files=normal` for the same signal at less I/O on SD-card hosts.
- Modified or untracked desktop build inputs (`desktop/src/**`, `package.json`, the lock-files, `vite.config.*`, `tsconfig*.json`) now force a rebuild even when their mtimes are older than the bundle — a file restored from an archive or written on a clock-skewed host would otherwise read as "current". Deployments without git keep the mtime heuristic so a tarball install does not rebuild on every start. The Python staleness check now compares the same build-config inputs the shell script always has, and neither side counts `desktop/node_modules` any more, so an `npm install` alone no longer looks like a stale bundle.
- The post-build marker step in `scripts/rebuild-desktop.sh` now clears a stale provenance marker when `git status` succeeds and reports a clean tree but `git rev-parse HEAD:desktop` cannot resolve (unborn `HEAD`, or a shallow/partial checkout missing the tree object) — an existing marker no longer survives unverified, matching the Python path's `if tree_sha and clean: record else: clear`.
- `/api/projects/{pid}/tasks/ready` now honours `blocked-on:<id>` labels alongside `task_relationships` edges: a task carrying a `blocked-on:<id>` label whose target is OPEN in the same project is excluded from the ready set; closing the target re-surfaces it
- The route's `?limit` query param is now honoured: clamped to `[1, 500]`, so `?limit=0` and `?limit=-1` no longer silently widen to unbounded or fall back to the default 50
- `ready_tasks` (and its migration twin) now scope `blocked-on:<id>` label joins to the same project as the labelled task, so a cross-project `blocked-on:<id>` label can no longer hide a task in the wrong project
- Merge attribution audit: the producer (`scripts/gate_merge.sh`) now logs the `mergeCommit` OID from `gh pr view <n> --json mergeCommit` instead of `headRefOid`, and takes the PR number from its own arguments instead of resolving from the current branch. It delegates the actual merge to `~/.taos-team/gate_merge.sh` when that fleet gate exists, so adopting this wrapper never bypasses the lead-blocked / CI / red-proof refusals; it falls back to a bare `gh pr merge` only when the gate is absent.
- Merge attribution reconciliation (`scripts/check_merge_attribution.py`) now enumerates merged PRs from the GitHub API (`gh pr list --state merged`) instead of `git log --merges`, so squash merges (which produce no merge commit) are no longer invisible by construction. It reconciles on the `mergeCommit` OID from the API -- the same key the producer records. A `--cutoff` flag (ISO timestamp or git SHA) keeps pre-adoption merges out of scope.
- `TestReadinessPollCrashDetection::test_readiness_fails_fast_when_proxy_crashes_at_startup` was environment-dependent: `LLMProxy.start()` resolves the litellm binary via `Path(sys.executable).parent / 'litellm'` before falling back to `shutil.which`, so any checkout whose venv has litellm installed would launch the real proxy and the test would time out. Extracted the resolution into `_resolve_litellm_cmd()` so tests can monkeypatch it directly, making the test deterministic regardless of what is installed in the venv.
- The Notifications settings panel is no longer a terminal dead end when the prefs fetch fails. It now renders a Retry control that re-issues the request through the same code path as the initial load, so a transient backend blip recovers without a full desktop reload.
- `git_log` now places the commit message as the last field so filenames containing the field separator cannot corrupt parsing.
- Committer install now runs `mkdir -p /root/.taos` before pushing the script, and disables versioning when directory creation fails.
- Agent version routes now fail closed with 403 when ownership cannot be resolved from the registry or agent config.
- `git_rev_parse` and `git_diff` now distinguish container execution failures from unknown revisions, returning 409 `container_unreachable` instead of 404 when the container is unavailable.
- `git_rev_parse` and `git_diff` now classify unknown revisions from git's actual diagnostics ("needed a single revision", "unknown revision", "bad revision", "bad object", matched case-insensitively) instead of a single guessed phrase that git never emits for these commands.
- The agent state `.gitignore` now also excludes `.env.*` variants (e.g. `.env.local`, `.env.production`), not just the exact `.env` filename, so they can no longer be committed and exposed through the version-diff route.
- Agent state revert now decides "noop vs. reverted" atomically, inside the container's state lock, instead of comparing HEAD before the lock is taken — a committer creating a new commit in that window could previously make the route falsely report `noop` without actually reverting.
- Deploy now reports `versioning=False` with a `versioning_error` when the committer script is missing, fails to push, or fails to launch via nohup, matching the existing behavior for other committer install failures.
- Agent state revert decides "noop" versus "reverted" inside the state lock, so a commit landing between resolving the requested version and the reset can no longer make the revert a silent no-op.
- Unknown revisions are reported as 404 whatever wording the installed git uses ("bad revision", "unknown revision", "ambiguous argument", "bad object") instead of 409 container_unreachable.
- A deployment whose auto-committer never starts now reports `versioning: false` with the reason, instead of claiming versioning is on while no commits will ever happen.
- The auto-committer now computes its changed-file summary from the staged index after `git add -A`, so a new untracked file (the common agent change) is named in the commit subject instead of falling back to a bare "auto-commit".
- `validate_config()` now rejects a non-mapping `wake_budget` (e.g. `[1]` or `[]`) instead of crashing with `AttributeError` or silently treating it as an empty config.
- `validate_config()` now requires `wake_budget.global_default` and per-agent/per-project values to be real `int` instances, explicitly rejecting floats (which `int()` truncates) and booleans. A one-character config typo such as `0.9` no longer silently disables all scheduled wakes fleet-wide.
- `get_fleet_wake_info` now catches `WakeBudgetStateError` per agent and degrades only the affected row (null `next_wake_epoch`, consumed:0, remaining:0, explicit `state: "damaged"` marker) instead of letting the exception propagate and take out the whole fleet report when `wake_budget.json` is damaged.
- Backend catalog startup no longer blocks the :6969 main app on the first probe pass, so a cold boot with unreachable configured model backends serves the first request immediately instead of waiting the full per-backend connect timeout (tsk-xjwolt; measured at 100s+ on a Pi 4 with three unreachable local backends). The catalog still reconciles every configured backend in the background, and callers that need the post-probe view can await `BackendCatalog.wait_initial_probe()` explicitly.
- `pytest tests/` run from the repo root no longer aborts collection with `Interrupted: 1 error during collection` (exit 2, zero tests run). `tests/test_core_deps.py` bound the shared core-dependency guard helpers with a bare `from conftest import`, a name resolved by `sys.path` order; on a full-suite run pytest put `tests/e2e/conftest.py` first, so the import resolved to the e2e shim (which lacks the helpers) and aborted the whole suite, while CI stayed green only because CI runs `pytest tests/ --ignore=tests/e2e`. The helpers are now loaded from `tests/conftest.py` by absolute file path, and a new regression sweep fails if any `tests/`.py file binds the bare `conftest` name again (tsk-xplzqy).
- A corrupt model re-download no longer destroys an existing valid install.
  `DownloadManager._download` promoted the `.part` stage file onto `task.dest`
  before running SHA256 and size validation; when the re-download's bytes
  mismatched, the good file at `task.dest` was first overwritten and then
  deleted, leaving the user with nothing. Validation now runs against the
  `.part` file first, and only on success is it atomically renamed onto
  `task.dest`. A mismatch unlinks the `.part` alone and leaves `task.dest`
  untouched. The `promoted` flag that tracked whether `task.dest` had been
  overwritten is no longer needed and has been removed.
- `_validate_download` now accepts a `path` parameter so callers can validate
  either the `.part` stage file or `task.dest` without re-reading a file whose
  streamed digest is already in hand.
- The torrent path in `_download_with_fallback` already validates before
  marking a task complete (it writes directly to `task.dest` and
  `torrent.download()` verifies SHA internally before returning), so the
  promote-before-validate defect never applied there.
- Hailo-10H `.hef` model variants now enforce their `hef_h10h` content pin: the `OllamaInstaller` compares the digest reported by `hailo-ollama pull` against the manifest's `hef_h10h` and fails the install on mismatch, so the pin is no longer decorative. The fabricated-digest denylist now also covers `hef_h10h`, and the manifest integrity test rejects hailo-ollama variants that declare neither an enforced pin nor the documented delegation.
- Outbound inference and framework-adapter calls now retry the transient failures they were silently dropping: connect timeouts (what a backend still booting produces), pool timeouts, read errors and HTTP 429 — with `Retry-After` honoured. An exhausted 5xx now raises `httpx.HTTPStatusError` instead of an internal type no caller could catch, and the retry loop is bounded by a total-elapsed deadline: it stops rather than starting an attempt the budget cannot cover, so it can no longer keep running after the channel hub has given up on the request. Every exhausted loop is logged, including one that runs out of budget on its first attempt.
- Checklist item events (`checklist.item.created`, `checklist.item.archived`) are now published at the project scope so project subscribers receive them, instead of being keyed on the task id where no subscriber listened.
- `archive_checklist_item` on a nonexistent item now raises a clean `ValueError` instead of a `TypeError` from indexing `None`.
- `tinyagentos/registry`: `AppManifest` is now a `pydantic.BaseModel`, so
  manifests from the external catalog repo are type-checked at the load
  boundary. A wrong-typed field (e.g. `requires: "ollama"` where a mapping
  is required, `context_window: "8192"` where an int is required) now
  raises `pydantic.ValidationError` *naming the offending field and the
  manifest path* instead of silently propagating the bad value into
  install code as `AttributeError: 'str' object has no attribute 'get'`.
  The catalog load loop also skips a single bad manifest with a named
  log message instead of aborting the entire store listing, so one
  malformed entry no longer takes the store offline.
- `tinyagentos/registry`: a non-mapping YAML manifest (list, scalar, or
  empty file) is now skipped before `from_dict` is called, and
  `from_dict` itself raises `pydantic.ValidationError` (via
  `PydanticCustomError`) for non-mapping input instead of crashing with
  `TypeError`. The `from_file` path also uses `PydanticCustomError`
  instances for `yaml_parse_error` and `manifest_not_mapping` instead
  of invented error types that crashed inside pydantic-core.
- `scripts/check_manifests.py`: the CI lint now validates every service
  manifest against the same `AppManifest` pydantic model the runtime uses,
  closing the mirror-image bug where a typo'd manifest slipped through
  the gate as a silent skip.
- `.github/workflows/doc-gate.yml`: the managed-backend manifest lint
  step now installs `pydantic` alongside `pyyaml` so the script can
  import the model.
- `scripts/manifest.schema.json`: published JSON Schema for `AppManifest`
  so third-party app authors can validate their catalogs against the same
  contract the runtime enforces.
- `POST /api/cluster/workers/update-all` now returns 202 with a job id immediately and runs the rolling update as a tracked background task. Poll `GET /api/cluster/workers/update-all/<job_id>` for progress instead of blocking the HTTP request for up to n x 300 s (R2-24).
- `gate-integrity.yml`: The base-ref resolution step sets `GH_TOKEN`, enables `set -euo pipefail`, and asserts the resolved base ref is non-empty, so a missing token or failing `gh api` no longer silently falls back to the default branch.
- `useOsEvents` decides whether the shared stream stays open from the subscriber map alone, read once the React commit has settled. The previous per-component "am I still mounted?" guard could close and immediately reopen the stream when one subscriber unmounted in the same commit in which another changed its kinds or a replacement subscriber mounted.
- Connection status is published through a shared snapshot, so a stream that keeps erroring while the browser retries no longer re-renders every subscriber on each repeated error.
- `useOsEvents` keeps the 128-id dedup window per subscriber rather than once for the shared stream, so a busy event kind can no longer evict a quiet subscriber's ids and make it handle a replayed event twice.
- A subscriber mounting while a reconnect is already scheduled no longer cancels it, so a view that mounts callers repeatedly against a down endpoint retries on the 5s-to-30s backoff instead of at mount frequency.
- Each subscriber's handler runs inside its own isolation boundary on the shared stream, so an app whose handler throws can no longer stop later subscribers from receiving that OS event (`events.lagged` included). The failure is logged and delivery continues.
- The delegation and skill-exec governance gates now deny (403) when `app.state.execution_policies` is absent instead of silently allowing the call. This covers governed calls only: an admin human session and an unclassified skill each return before the store is consulted, unchanged by this fix. An absent store indicates a misconfigured app because the startup wiring in `app.py` sets `app.state.execution_policies` unconditionally.
- Error and failure messages in the Updates, Logs, Themes, and Users settings panels are now announced to screen-reader users via `role="alert"` live regions, matching the pattern already used in the Notifications and Account panels. Routine success/progress text is intentionally left without a live region so it does not train users to ignore alerts.
- Agent state version revert now restores the full snapshot at the target commit instead of inverting a single commit. `git_revert` runs `git revert --no-edit <sha>..HEAD` so the tree matches the requested commit's state, and the revert endpoint asserts `README.md` remains with `notes.txt` absent after the operation.
- `.taos/trace/` is now excluded from the agent state gitignore before the initial commit, preventing trace directory contents from being staged into git history.
- Remote agent container targets are persisted in the agent record and used for all version operations, so remote-deployed agents resolve to `<remote>:taos-agent-{name}` instead of the unqualified local name.
- The `sha` path parameter on version diff and revert routes is validated against `^[0-9a-fA-F]{7,40}$` (hex is case-insensitive) before it reaches any git argv, preventing argument injection such as `--output=.bashrc`.
- Pin container provisioning to incus/LXC backend; the global backend
  resolution previously selected Docker on docker-capable hosts, which
  broke provisioning because the card scopes creation to incus/LXC only.
- Move non-approve request creation inside the policy-check lock so
  concurrent over-threshold requests cannot both insert and escalate
  outside the atomicity guard.
- Sanitize canonical_id when composing the container name so invalid
  characters cannot leak into incus instance names.
- Propagate create_container, set_env, and destroy_container failures
  instead of swallowing them silently.
- Meshtastic (LoRa) send path now degrades rich replies via `_degrade` instead of bypassing it, so dropped-element notices reach the transport; the `[part N/M]` denominator is derived from the actual chunking and always equals the emitted part count.
- install-server.sh now POSTs `/api/system/hardware/refresh` (the route is
  POST-only; a GET returned 405 and `curl -sf` swallowed it, producing the
  silent "hardware verification skipped" line from taOS #2 on the Orange Pi
  5B run). The verification step also retries for up to 30 s so a controller
  still finishing first-boot init is no longer treated as a failure, and on
  a persistent empty response it now dies loud with the journal tail and a
  checklist of what to inspect (`/dev/rknpu`, data dir writability, journal)
  instead of silently exiting 0. The detected profile (NPU type, device,
  profile_id) is now surfaced in the success banner so a tester can confirm
  at a glance that the NPU was recognised.
- Debian trixie / Armbian trixie fallback to Docker's official repo: writes the keyring 0644 (was destination-umask, often 0600), bounds the gpg download with `--connect-timeout 15 --max-time 60`, removes distro `docker.io`/`containerd`/`runc` before installing `docker-ce` so dpkg is not left half-configured, distinguishes missing-package from install-failure in `_apt_install_compose` (rc=2 vs rc=1) so a real apt error is no longer silently masked by a repository swap, and the fingerprint-mismatch warning now points operators at the docker.com gpg URL + rotation policy.
- A failed trixie fallback no longer leaves the host with no Docker at all. The fallback removes the distro `docker.io`/`containerd`/`runc` to make room for `docker-ce`, and the installer had just installed `docker.io` a moment earlier — so a failure on `apt-get update` or the `docker-ce` install (unsupported architecture, proxy, held deps) ended the install with the runtime gone. It now records which of the three were actually installed and reinstalls exactly those on both post-removal failure paths, retrying once after an `apt-get update` and warning with the manual command if even that fails.
- The fallback's rollback no longer destroys a host's own Docker apt config. A pre-existing `/etc/apt/keyrings/docker.asc` or `/etc/apt/sources.list.d/docker.list` (an internal mirror, a pinned suite, a corporate signing key) is now backed up before it is overwritten and restored byte-for-byte when `apt-get update` or `apt-get install` fails; only files this invocation created are deleted. Previously the rollback `rm -f`'d both paths unconditionally, so a failed fallback silently wiped a working third-party repository setup.
- The JSON agent-import endpoint (`POST /api/agents/import`) no longer persists a caller-supplied dict verbatim. Operational/privileged keys (`llm_key`, `permitted_models`, `registry_canonical_id`, `can_read_user_memory`) are stripped via a Pydantic allowlist model, and the agent name is now slugified with the same rule the create route uses, so a bundle naming an agent "My Agent" lands under container-safe slug `my-agent` instead of an un-routable key.
- Desktop rebuild trigger now checks provenance (desktop/ tree SHA) before the mtime fallback, preventing a false-positive full npm rebuild on fresh bundle installs when source mtimes are misleading.

### Security

- Skill-exec agent identity is now derived from the presented local-token
  credential binding, not from the request body. A deployed agent passing
  another agent's handle in the body is rejected with 403 instead of being
  served. `_resolve_agent_workspace`, `_capture_tool_receipt`,
  `_check_execution_policy`, notes and todo tools all key off the same
  credential-bound `agent_name`.
- Rate limiters can no longer be made to exhaust memory. The per-key registries
  behind the unauthenticated project-invite redeem, the cluster manual-claim,
  the routine webhook and the client-log capture grew without bound, so a
  caller with a large address range (an IPv6 /64 is 2^64 keys) could push the
  controller into an out-of-memory kill on a small board. (The peer routes
  registry already evicted opportunistically at 2000 entries; it carried the
  same brute-force and clock-step defects as the others, described below.)
  All five now share one bounded limiter in `tinyagentos/rate_limit.py` and
  evict the least recently used key once 2000 are tracked.
- Theme (`.taostheme`) installs and backup restores are now guarded against archive bombs: a shared `safe_archive` helper caps the declared uncompressed total, per-member size and member count before anything is read or written, and a backup tarball is now extracted under the path-safe tar filter (an unsafe member fails the restore instead of being silently skipped). A tarball's headers are judged one at a time as they arrive, so a compressed member over the cap is rejected without being decompressed first.
- Theme, backup and userspace-app uploads (32 MB, 64 MB and 64 MB) are refused with `413` while the request body is still arriving, so an oversized upload is no longer spooled to temporary storage before the handler sees it.
- A `.taostheme` member resolving to the theme directory itself (for example `.`) is now rejected as an unsafe path instead of crashing the install.
- A tar member declaring a negative size (a base-256 size field on a directory or symlink header, which CPython's `tarfile` hands through intact) is now rejected outright instead of clearing the per-member cap and pulling the running total down to buy headroom under the cumulative cap.
- The upload body cap now keys on a normalised request path, so `/api/restore/` and `/api//restore` are refused with `413` on the first hop rather than answered with a redirect or 404 that a non-redirect-following client would be left with.
- Skill-exec agent identity now uses per-agent local tokens minted at deploy
  time instead of the shared host token. Each deployed agent receives its own
  distinct `TAOS_LOCAL_TOKEN` bound to its name, so concurrent deploys can no
  longer overwrite each other's credential binding. The shared host local token
  remains valid for admin/system callers but is no longer bound to any agent
  name, so an unbound local-token caller falls back to the body-asserted
  behaviour (or is denied where the credential mismatch check applies).
  `request.state.agent_name` set by the auth middleware is reliable for callers
  presenting a minted per-agent token; the shared host token and agents
  deployed before this change (until their next redeploy) remain unbound.
- Regenerated Mac .app build lock to fix CVE-2026-69247 (PKCS#7 EnvelopedData Bleichenbacher) and PYSEC-2026-3552/3553/3554. Upgraded cryptography to 50.0.1 from 49.0.0. Lock was regenerated wholesale with uv pip compile.
- Device bearers are refused gate-kind (execution, delegation, app-grant) decisions on `POST /api/decisions/{id}/answer`; the phone remains a notification surface, not an approval channel for privileged grants (#tsk-dpnkab).
- Non-admin members can no longer read or change global resources they were never meant to reach: the secrets keystore (`/api/secrets` get/list/add/update/delete), controller restart and AI-stack restart (`/api/system/restart/prepare`, `/api/system/ai-stack/restart`, remote `prepare-shutdown`), provider create/patch/start/stop/delete (with `api_key` redacted from `GET /api/providers` for non-admins), MCP server start/stop/restart/uninstall, config, env, permission attach/detach and tool calls, and the cluster fleet mutations (`DELETE /api/cluster/workers/{name}`, `deploy`, `remote`, `move`, `route`, `promote-archived`) now answer `403 forbidden` unless the caller is an admin session or holds the host local token. A member still reads the granted secrets of, and mints Agent-as-a-Model consent keys for, agents it owns (ownership resolved through the agent registry); single-user installs are unaffected.
- The graceful-shutdown hook no longer keeps its dedupe stamp in a world-writable directory: any local user could plant that file and silently stop taOS draining agents on every restart and reboot. The stamp now lives in the service's own runtime directory (`/run/taos`, mode 0750, or `data/` on installs without systemd), carries its own timestamp so the age check works on macOS and BSD too, and the drain runs rather than being skipped if no private location is available.
- Fixed the stamp age check suppressing the drain indefinitely when the recorded epoch is in the future (RTC-less Pis routinely step the clock forward after an NTP sync post power-cut): the age is now floored at zero before the dedupe window is applied.
- Closed a gap where a candidate stamp directory carrying a POSIX ACL (RuntimeDirectoryMode does not strip a pre-existing one) could still grant group/other write while reading as private (0750) in plain mode bits: the private-directory check now rejects any `ls -ld` mode string marked with a trailing `+` (ACL present).
- Agent access requests and scope requests now enforce their per-identity pending cap atomically, inside the insert. A burst of concurrent requests could previously all read the same pre-insert count, all pass the check and all be stored, letting one agent flood the approver's queue past the cap that exists to stop it.
- UnifiedPush push-token endpoints now validate URLs against SSRF guards before storing or sending. Loopback, link-local, multicast, reserved, and unspecified addresses are refused at registration and again at send time. Decimal-encoded and IPv4-mapped IPv6 loopback forms are also blocked. RFC1918 ranges (and their IPv6 unique-local equivalent, `fc00::/7`) remain allowed for self-hosted LAN use cases; the CGNAT range `100.64.0.0/10` and deprecated IPv6 site-local `fec0::/10` are always blocked, because our own A2A bus lives in CGNAT space. (#tsk-kqryuo)
- `scripts/check_merge_attribution.py` now bounds every `gh` call with a timeout (`--gh-timeout`, default 60s), so a hung `gh` surfaces as an infrastructure error instead of hanging CI, and it warns on unparseable audit lines instead of skipping them in silence.
- `scripts/check_merge_attribution.py` now reconciles on the `(repo, sha)` pair rather than `sha` alone. The default audit log is one file shared by every repo the fleet merges, so an entry written for a fork or mirror carrying the same commit OID could previously stand in as proof for an unattributed merge elsewhere. It also skips audit lines that are valid JSON but not objects (`null`, arrays, strings, numbers), which used to crash reconciliation with an `AttributeError` that was not mapped to an infrastructure error.
- `scripts/gate_merge.sh` no longer records `"sha":"unknown"` when the post-merge `gh pr view` lookup fails. The reconciliation keys (`sha`, `repo`) are left empty, an explicit error names the PR, and the wrapper exits 65 -- a successful merge whose audit entry is incomplete can no longer read as a clean run.
- `scripts/gate_merge.sh`'s fallback JSON encoder (used when `jq` is absent) no longer lets a missing `python3` crash the script with a raw shell error before its own "neither jq nor python3 is available" handling can run; a failed audit-log append now also surfaces as the documented exit 65 instead of a silent write failure.
- `scripts/gate_merge.sh` now surfaces `gh`'s own error text ("gh said: ...") in the INCOMPLETE audit entry message when the post-merge `mergeCommit` or repo-slug lookup fails, instead of discarding it (`2>/dev/null`) and leaving the operator with no way to tell an auth failure, rate-limit, network error and a missing PR apart.
- `scripts/check_merge_attribution.py` now warns when an audit entry has a `sha` but no `repo` field, instead of silently excluding it from reconciliation with nothing pointing at the cause (producer bug or schema drift, distinct from a legitimate other-repo entry).
- `scripts/check_merge_attribution.py` now errors (`EXIT_ERROR`) when `gh pr list` returns exactly its `--limit` of 1000 records, instead of silently treating a possibly-truncated result as the complete merge history.
- `scripts/check_merge_attribution.py`'s `main()` now maps `OSError` (a missing `gh`/`git` binary, or an unreadable audit file) to `EXIT_ERROR` alongside `RuntimeError`, instead of letting it escape as an uncaught traceback that exits 1 and reads as "merges lack audit entries".
- App Studio's publish/install source scan now flags indirect eval (`window.eval(...)`, `globalThis.eval(...)`, `self.eval(...)`), unquoted dangerous URL schemes in HTML attributes (`<a href=javascript:...>`), and sandbox-escape references reached through a global-object prefix (`globalThis.top`, `self.parent`, `self.opener`). All three slipped past the previous patterns.
- `Gate integrity` workflow (`.github/workflows/gate-integrity.yml`) runs on
  `pull_request_target` from the base ref and fails any PR whose diff touches
  `.github/workflows/`, `.github/scripts/`, or `scripts/check_*.py` unless it
  carries the human-set `gate-integrity-allow` label. This closes the class
  defect where `pull_request`-triggered gates checked out the merge ref and ran
  their own checker from it, letting a PR edit its gate to always-exit-0 and
  green-pass the check that gated it. The integrity check inspects the PR diff
  via the GitHub API only and never checks out or executes PR code.
- API responses under `/api/` and `/agent/` now carry `Cache-Control: no-store` unless the handler set its own policy, so per-user JSON (account data, secrets metadata, grants, project files) can no longer be held by a shared proxy cache or replayed from the browser's back/forward cache. SSE streams keep their `no-cache` and static assets keep their long cache.
- `taos rollback` no longer executes its own state file. `scripts/rollback.sh` used to `source <install>/.taos-rollback`, which runs the file as bash — and the script escalates with `sudo` to restart the service in the same run. The file lives in the install dir, which the installer chowns to the `taos` service account, so anything able to write as `taos` (a compromised agent container with a bind mount, an updater bug, a partial write after a power cut) could get its shell executed with root. The record is now parsed line by line as data, `prev_sha` is accepted only when it is a full 40- (or 64-) character object name, and a recorded branch is restored only when `git check-ref-format` accepts it and it does not start with a dash — git considers `refs/heads/--force` a valid ref, but `git checkout -B --force` would read it as an option.
- Rotating an agent identity's tokens (`POST /api/agents/registry/{id}/rotate-tokens`) now also kills the surfaces that authenticate by identity alone. `check_agent_identity` skipped the `token_min_iat` cutoff that `check_agent_scope` and `check_agent_scope_for_project` both enforce, so a superseded token still passed on the routes that need no scope grant -- creating a scope request (the one route whose purpose is asking for more privilege), the agent decisions routes, container-provisioning requests and the auth-request flow. A rotated token is now refused there, and on an unrouted path it gets the dead-credential 401 instead of the wrong-URL 404.
- DockerInstaller now substitutes the per-app `{secret_key}` placeholder in
  `install.env` values (not just `config_files` content), so Linkwarden's
  `NEXTAUTH_SECRET` is a stable 64-hex-char secret persisted in
  `<app_dir>/.secret_key` instead of the shipped default. Previously every host
  ran Linkwarden with the publicly-known session-signing secret `changeme`,
  allowing session forgery.
- Linkwarden manifest restores the required `DATABASE_URL: "postgresql://postgres:postgres@localhost:5432/linkwarden"`.
  Database is now explicitly declared in the manifest to match upstream Linkwarden.
- Generated docker-compose.yaml and config files are now written with permissions 0o600
  to protect any secret substitutions (previous default umask 0o644 exposed secrets
  in the live session-signing key). Applies to all files written by
  `_write_config_files` and `install` that contain a `{secret_key}` substitution.
- Notification titles and messages are now HTML-escaped in the notification dropdown, so markup in agent-supplied text (such as the reason on a secrets-broker access request) can no longer run as script in the taOS dashboard.
- Agent state versioning now versions an explicit allowlist of state paths (workspace, memory, per-framework AGENTS.md) instead of denying a list of secret patterns, so framework config carrying API keys and bridge tokens (`.hermes/config.yaml`, `.openclaw/env`), shell history, credential files and cache trees can no longer enter the agent's git history.

### Changed: the sign-in and setup pages carry the taOS wordmark

- The auth pages previously showed a drawn mark — a rounded square with a
  centre dot and an X through it. An X in a box on a sign-in screen reads as
  an error badge or a close affordance rather than a logo, so the brand is now
  set as type: the product name in the page's own font stack.
- The mark stays plain ASCII, so the pages keep the property the inline SVG was
  introduced for — no webfont to fetch, and no code point that can render as a
  missing-glyph box on a device without a covering font. These pages are
  deliberately JS-free and CDN-free, so a text glyph would have lost that.
- The setup page keeps its welcome in the subheading; no copy is dropped.
- `tests/test_auth_brand_mark.py` anchors on the `.brand` block rather than the
  whole page, and fails both on the drawn mark returning and on a regression to
  a bare Unicode glyph.

### Docs

- Fixed a stale docstring on `notifications_push._build_payload` that claimed the service worker only reads `event.notification.data`; it now explains why `image` is also copied to the top level.

### Fixed: Add agent self-service routes to _AGENT_TOKEN_PATHS

- Added `/api/agents/me/models` and `/api/agents/me/model` to `_AGENT_TOKEN_PATHS` in
  `tinyagentos/auth_middleware.py` so that agent LiteLLM keys (Bearer tokens) are accepted
  for agent self-service routes. Previously, AuthMiddleware rejected these requests with 401
  before the handler could authenticate the agent key.

### Fixed: Immutable version-pinned installer URLs and abort-on-hash-mismatch for catalog installs

- `app-catalog/agents/deer-flow/scripts/install.sh`: Switched uv installer fetch from mutable `astral.sh/uv/install.sh` to immutable `github.com/astral-sh/uv/releases/download/0.12.10/uv-installer.sh`. Added `_fetch_and_verify` helper that exits 1 with URL/expected/actual on hash mismatch. Recorded measurement command and hash.
- `app-catalog/agents/openclaw/scripts/install.sh`: Replaced mutable NodeSource `setup_22.x` script with direct apt repo pinning (replicating its key+repo actions). Added `_fetch_and_verify` helper that exits 1 with URL/expected/actual on key hash mismatch.
- `app-catalog/streaming/code-server/Dockerfile`: Switched installer fetch from mutable `code-server.dev/install.sh` to immutable `raw.githubusercontent.com/coder/code-server/v4.135.0/install.sh`. Recorded measurement command and hash.
- `tests/scripts/test_audit_s2_22.py`: Added `test_sha256_pinned_urls_are_versioned` (mutable-URL guard, fails when a pinned sha256sum -c fetch has no version token in its URL). Added `test_hash_mismatch_aborts_with_url` (verifies non-zero exit and URL presence in stderr on hash mismatch).

### Fixed: split-brain layer-2 protection is now actually reachable

- The controller echoes its current generation in both POST /api/cluster/workers
  (register) and POST /api/cluster/heartbeat responses, and WorkerAgent captures
  and returns it on every subsequent request. Previously the worker-side capture
  read a field no response contained, so the layer-2 guard (manager.py:110-115,
  294-299) could never fire. A missing echo is now a logged warning, never a
  silent downgrade. End-to-end test: a heartbeat carrying a stale generation is
  rejected 404.

### Fixed: the touchscreen kiosk now reaches the PIN sign-in screen

- The desktop SPA no longer renders a sign-in form of its own. When
  `/auth/status` reports the install is configured but the visitor is not
  authenticated, `LoginGate` hands off to the server-rendered `/auth/login`,
  carrying the current path as `next` so the user returns where they started.
- This closes a split that had teeth on a keyboard-less device. `/desktop` is in
  `EXEMPT_PATHS`, so the session gate at `auth_middleware.py:636` never fires for
  the shell HTML: the kiosk booted straight into `LoginGate`'s password-only form
  and never saw the PIN keypad added on `/auth/login`. On a touchscreen Pi with no
  keyboard that is a hard lockout — the device cannot be signed into from its own
  screen. Reproduced on the real pitop kiosk before and after.
- `auth_middleware.py:285-292` already documented this handoff as the contract;
  `LoginGate` had simply drifted away from it. The invite flow is unaffected —
  `POST /auth/login` creates a session for a pending user and returns them to
  `/desktop`, where `/auth/status` reports `needs_onboarding` and the SPA renders
  the completion screen, exactly as `routes/auth.py` already described.
- A repeat redirect is guarded: if the SPA comes back from `/auth/login` still
  unauthenticated it stops and offers a link instead of bouncing between two URLs
  forever, which on a kiosk would be worse than any login form.

### Fixed: workers on a beta can finally see a newer beta

- The worker update check compared versions by throwing away everything after
  the first `-`, so every beta of a release collapsed onto the same numeric
  tuple. taOS ships `1.0.0-beta.N`, so no beta ever saw a newer beta and no
  worker could cross from a beta to the GA of the same release; a version pin
  inherited the same blindness and accepted `1.0.0-beta.99` under a
  `1.0.0-beta.40` pin. Version strings are now parsed with `packaging`
  (PEP 440), so pre-releases order against each other and below their GA.
- The update channel of a version is now read from its parsed pre-release
  segments instead of by searching the raw string, so a GA release carrying
  build metadata such as `1.0.0+devbuild` is no longer classified as a dev
  build and withheld from stable- and beta-channel users.
- The optional-app catalog no longer reports a permanent "update available"
  for an app whose recorded version cannot be parsed: it used to fall back to
  `(0, 0, 0)`, which read as older than everything. An unrecognisable recorded
  version now means "no update", and a recorded pre-release correctly reads as
  older than the GA of the same release.
- `packaging` is now a declared runtime dependency; it was previously reachable
  only through the `proxy` extra and the dev group, so a bare `uv sync` did not
  install it.

### Internal

- The `token_min_iat` rotation-cutoff check was copy-pasted into three places in `tinyagentos/agent_token_auth.py` (`_verify_agent_scope`, `check_agent_identity`, `check_agent_project_grants`); extracted into a single `_enforce_rotation_cutoff` helper so a future change to the cutoff semantics cannot silently apply to only some of them.

### Migration notes

- **Backward compatibility:** SQLite remains the default engine. No existing stores will change behavior unless they explicitly set `ENGINE = Engine.POSTGRES`.
- **Future migration:** This change provides the foundation for migrating stores to Postgres in follow-on slices (tsk-wdplga migration 2+). The engine selection logic is now in place and tested.

### Tests

- Fixed a CI flake in `tests/test_merge_attribution.py`: two assertions checked that an excluded PR number ("41") was a bare substring of `result.stdout`, but the fixture commit shas are generated at runtime, so a sha for the in-scope PR could coincidentally contain "41" and fail the assertion for a reason unrelated to the actual reconciliation logic. Both now assert on the exact `"#41"` PR-reference token the checker prints, which cannot collide with a hex sha substring.

## [1.0.0-beta.51] - 2026-09-07

Security hotfix release cut from 1.0.0-beta.50. Every install with more than one user account should update.

### Security

- Non-admin members can no longer read or change global resources they were never meant to reach: the secrets keystore (`/api/secrets` get/list/add/update/delete), controller restart and AI-stack restart (`/api/system/restart/prepare`, `/api/system/ai-stack/restart`, remote `prepare-shutdown`), provider create/patch/start/stop/delete (with `api_key` redacted from `GET /api/providers` for non-admins), MCP server start/stop/restart/uninstall, config, env, permission attach/detach and tool calls, and the cluster fleet mutations (`DELETE /api/cluster/workers/{name}`, `deploy`, `remote`, `move`, `route`, `promote-archived`) now answer `403 forbidden` unless the caller is an admin session or holds the host local token. A member still reads the granted secrets of, and mints Agent-as-a-Model consent keys for, agents it owns (ownership resolved through the agent registry); single-user installs are unaffected. The MCP part of this closes a host command execution reachable by any non-admin member (store an arbitrary `cmd` for an installed server, then start it), reported by MR-pentestGuy (GHSA-5ppx-4q94-9v48).
- Fixed a path traversal in `POST /api/import/upload` and `POST /api/import/embed`: a client-supplied filename such as an absolute path or `../` escaped the upload directory, letting any authenticated user write or read files the server process can reach, including the auth store. Names must now be a plain basename that resolves inside the upload directory, and `agent_name` is validated. Reported by EQSTLab (GHSA-rwrp-hfc4-qg2w).

## [1.0.0-beta.50] - 2026-08-21

### Added

- Agent-accessible todo-list tools: `todo_list_lists`, `todo_add_item`, `todo_set_done` (#2035).
- When an agent asks a question over the openclaw chat bridge via `request_decision`, a read-only `{kind:"decision", decision_id}` content block is now attached to the in-flight chat message and rendered inline in the MessagesApp chat. The question text, option list (disabled buttons), decision type, and current state (open or answered with the chosen option, answerer, and timestamp) all show directly in the conversation. Re-opening a thread re-fetches the decision, so an already-answered question renders in its resolved state instead of a stale open prompt. Decisions raised outside a chat context do not produce a block.
- **Agent-as-a-Model turn execution**: `POST /v1/chat/completions` now drives a
  real one-shot agent turn (consented agent → opencode host-server seam →
  OpenAI ChatCompletion envelope) instead of returning 501. Per-agent opencode
  server cache so concurrent agents do not churn a shared singleton. Missing
  user message returns 400 (not 502); `stream` requires an explicit JSON
  boolean (#2195).
- Cluster nodes can be revoked, blocked and unblocked from the Cluster UI, matching what was already possible for devices. Revoke kills a node's signing key and lets it re-pair; block additionally refuses re-pairing until an admin unblocks it; unblock clears the block but leaves the old key dead, so the node must re-pair for a fresh one. Revoked and blocked nodes are marked offline at once so the scheduler stops routing work to them, while staying visible in the worker list so they can be unblocked (#2410).
- Select decisions can be answered off-menu: single-select and multi-select decisions accept an `other_value` free-text answer plus an optional `note`, so a decision whose declared options do not fit no longer forces a wrong choice. Declared option values are still validated, combining `value` with `other_value` on a single-select is rejected, and the free-text entry is appended for multi-select (#2412).
- Hailo-10H .hef model catalog manifests for qwen2.5-1.5b, qwen2.5-coder-1.5b, qwen2-1.5b, llama-3.2-3b, and deepseek-r1-1.5b, using the hailo-ollama backend with pinned sha256 + download_url.
- Added per-agent `memory_mode` (`both`, `framework`, `taosmd`) surfaced in the Agents deploy wizard, persisted on the agent record, and injected as `TAOS_MEMORY_MODE` at deploy time.
- Added three onboarding guides in `docs/agent-manual/` (one per mode) and linked them from the manual index. Compiled manual stays under the size budget.
- Added tests for mode persistence, deploy-time env injection, and the conflict rule (taOSmd authoritative for durable facts, framework memory for live working set).
- DecisionBlock tests updated to assert new interactive contract: `disabled={!isOpen}` — open decisions render enabled controls that submit answers, and non-open decisions render disabled controls
- Added first-answer-wins test verifying that submitting a second answer is rejected when decision status is no longer "pending"
- Marked Text Editor, Image Viewer, and Media Player as file handlers (tier 4) so they no longer appear in the launcher but remain openable programmatically.
- Files now routes text files to Text Editor, images to Image Viewer, and audio/video to Media Player on double-click or context menu open.
- Decision blocks in chat now support clickable option buttons for answering decisions directly. Users can select options or enter text answers in the chat interface, which uses the same API endpoint as the Decisions app.
- Agents can no longer answer their own decisions. The answer endpoint now validates that decisions are answered by the human user assigned to them, not by the agent who created them.
- First-answer-wins logic ensures that when the same decision is answered concurrently from both the chat and Decisions app surfaces, exactly one answer is recorded. The second answer attempt receives a clean rejection error.
- Both chat and Decisions app surfaces update live in real-time through the existing broker/SSE machinery. Answering in chat resolves the card in an open Decisions app without requiring a page refresh, and vice versa.
- Decision answers now propagate live across all open surfaces via SSE. When a decision is answered in one surface (chat or Decisions app), other surfaces update immediately without requiring a page refresh.
- Concurrency safety: first-answer-wins enforcement via atomic store-level `UPDATE ... WHERE status = 'pending'`. Concurrent answer attempts from multiple surfaces resolve to exactly one recorded answer; subsequent attempts receive a clean 409 response with no duplicate event broadcast.
- A `scripts/check_bot_review.py` gate that fails (exit 1) when the only CodeRabbit output on a PR is a rate-limit stub, so the merge path no longer treats a passing "Review rate limited" check as a real review. Runs on every PR targeting `master` or `dev` via `.github/workflows/bot-review-gate.yml` (tsk-vzzv62).
- Chat sidebar regroups channels into Channels, Agents-DMs, and Direct Messages sections with live presence dots (working/live/idle) and an accent rail on the active channel (#tsk-z4wn3x).

### Fixed

- Closing a claimed task is refused unless you are the claim holder, the project lead, the project owner, or a session admin; any other caller now gets 409 instead of silently closing someone else's card (#2287).
- Notes list now filters by `kind="note"` so todo-list docs no longer leak into the Notes UI (#2325).
- Fixed invalid JSX `aria-label=Notes` (missing quotes) that blocked the SPA build (#2325).
- Projects Lists tab no longer uses the browser's native `prompt`, `confirm` and `alert`: creating a list, deleting a list, removing an entry and viewing an entry's original text now use real in-app dialogs, so they are keyboard-accessible, themable and cannot be suppressed by the browser. The rail and entries panel also stack vertically under 768px instead of being squeezed side by side (#2411).
- `GET /api/decisions/agent` now scopes an agent's decision list consistently with how it is allowed to raise them: a global (null-project) grant returns null-project decisions rather than every project's, matching the posting rule. The project filter is also pushed into the store query for the global and single-project cases so the row limit applies after scoping instead of before it (#2194, #2417).
- The auth middleware's agent Bearer allowlist now covers `GET`/`POST /api/projects/{project_id}/tasks/{task_id}/checklist-items`, so a registry JWT reaches the handlers' scope checks (`project_tasks_create` on POST/create, `project_tasks` on GET/list) instead of being refused 401 at the gate. Live since the checklist routes merged (#2674); DELETE and per-item subpaths stay session-only (#2430).
- An unclean shutdown could leave `data/.auth_user.json` the right size but full of NUL bytes, which taOS read as "no accounts exist" and answered with the first-run onboarding screen — and completing that form overwrote the real accounts. The account store, session store, legacy password file and local auth token are now written atomically (temp file, fsync, rename, directory fsync), and an account store that exists but cannot be parsed fails closed: the install still reports itself configured, onboarding is refused, and `/auth/status` returns `store_error: "unreadable"` while every other request answers 503 `account_store_unreadable` instead of a plausible empty result. Recovery steps are in `docs/runbooks/controller-rescue.md` (#2502).
- Fixed hardware_tiers YAML indentation in five HEF manifests (deepseek-r1-1.5b, qwen2-1.5b, qwen2.5-1.5b, qwen2.5-coder-1.5b, qwen3-1.7b) so tier keys nest under hardware_tiers instead of parsing as null.
- Removed two a8w4 variants with fabricated sha256 pins (llama-3.2-1b/a8w4 and qwen3-1.7b/a8w4) that returned HTTP 404 on their download_url.
- Extended the model manifest integrity test with a denylist of known-fabricated digests and a hardware_tiers nesting check (no stray tier keys at variant level; hardware_tiers must be a non-empty mapping).
- A stale or incomplete package install (an empty directory left on `sys.path` that Python treats as a PEP 420 namespace package) can make a transitive dependency like `sniffio` importable but attribute-less. anyio calls `sniffio.current_async_library` on every async test, so the partial module raised `AttributeError: module 'sniffio' has no attribute 'current_async_library'` across 562 unrelated tests on a single shard, reddening a 3-file frontend PR. Measurement confirmed the cause: `sniffio.__file__` was `None` with a `NamespaceLoader` in `__spec__` (the empty-directory signature), and `sniffio` was absent from the resolved package set. The test suite now runs a session-start guard (`_verify_core_deps` in `tests/conftest.py`) that checks a data-driven table of core deps (`sniffio`, `anyio`, `httpx`, `httpcore`, `idna`, `certifi`, `pydantic`, `sqlcipher3`, `fastapi`) and fails loudly at session start with `__file__` / `__path__` / `__spec__` / installed-package diagnostics instead of letting the defect surface as hundreds of opaque tracebacks. The guard is generic -- keyed on the (module, required-attributes) table, not on the string `sniffio` alone -- so any importable-but-attribute-less core dep is caught.
- `POST /api/store/install-v2` now validates `target_remote` at the API boundary before it is interpolated into backend daemon URLs (`resolve_rkllama_url`, LXC remote addressing). Hostile strings containing `:`, `/`, `?`, `#`, or `@` are rejected with HTTP 400 and a named `invalid_target_remote` reason, preventing SSRF-shaped installs or silent mis-routing to unregistered workers.
- Removed `tinyagentos/containers.py`, which had been unreachable dead code since the `containers/` package landed. Edits to the shadowed module silently no-opped at runtime; the package copy at `tinyagentos/containers/__init__.py` is what all imports resolve to.
- The `seed` parameter for `generate_image` is now forwarded from the skill-exec runtime to the image generator and is advertised in the agent-facing tool schema, so reusing a returned seed to iterate on a liked image actually holds the seed instead of silently producing a fresh random one (#tsk-47ix5m).
- `scripts/collate_changelog.py` is now idempotent across partial failures: if a run dies between writing the new version section and unlinking consumed fragments, a rerun detects the existing `## [<version>]` header and skips the duplicate insert. Only leftover fragments whose content already reached `CHANGELOG.md` are consumed; a fragment that landed after the failed run is kept and the rerun exits non-zero naming it, instead of silently deleting a release note that was never folded.
- The doc-gate content-blindness defect: a per-doc list of required section headings is now asserted present in the working tree. A `docs/agent-coordination.md` emptied of its protected API-surface sections now fails the `invariants` check instead of passing the gate indefinitely.
- Image Viewer and Media Player now display the basename of a routed file URL in their title bar: the final URL segment is decoded before the directory path is stripped, so a nested route like `nested/photo.png` no longer leaks into the displayed file name.
- `list_pending` in `device_pair_requests_store.py` now selects only `_SAFE_COLS` instead of `SELECT *`, preventing leakage of columns outside the allowed set such as `verify_code`.
- Fixed DecisionBlock free_text textarea no longer posts on every keystroke; onChange now updates local state only, Enter submits once, and a visible Submit button is provided
- Surface answerDecision errors inline instead of unhandled promise rejections
- Removed unreachable duplicate conditions in dayLabel
- `GET /api/decisions/agent` no longer leaks project-scoped decisions to an agent holding only a global (null-project) `decisions_write` grant. The store layer now treats an explicit `project_id=None` as `IS NULL` instead of silently omitting the filter, so a global grant returns null-project decisions only. The human-facing `GET /api/decisions` route preserves its existing "no project filter" behaviour when `project_id` is absent from the query string.
- Consolidated Hailo-10H HEF variants into existing model manifests (qwen2.5-1.5b, qwen2-1.5b, qwen2.5-coder-1.5b, deepseek-r1-1.5b, llama-3.2-1b, llama-3.2-3b, qwen3-1.7b); dropped unverified hef_h10h pins and removed bare download_urls from hailo-ollama-pull variants.
- NotificationsPanel now correctly shows error messages when the prefs fetch fails instead of displaying a permanent loading state. Added a `loaded` flag to track when the initial fetch has settled, distinguishing between genuine loading and error states.
- pin-aware HF listing now uses the revision-path `blobs=true` endpoint so nonexistent revisions 404 and real file sizes are returned
- per-file `lfs.sha256` verification after download catches corrupted or mismatched shards
- paligemma-2 `file_set_hash` recomputed with real sizes from the pinned-revision blobs listing
- Device pair-request creation: enforce the pending cap atomically so concurrent requests cannot bypass it.
- Device pair-request creation: return 409 Conflict when no instance admin exists, instead of silently creating an unapprovable request.
- DecisionBlock free_text textarea now stores the raw value and trims only at submit, so trailing spaces and Shift+Enter newlines are no longer eaten on every keystroke
- DecisionBlock now surfaces the server's exact error reason (e.g. `already answered or not pending`) in the inline alert instead of the generic "Could not record answer."
- project_notes scope now requires project_id binding when granting via auth request approve, rejecting the unbound approvals that previously minted inert grants (approval looked successful while the agent silently had no notes access)
- Subagent worker exceptions are now propagated through `await_subagent` instead of being swallowed; a failed subagent raises the original exception at the caller, making failures observable rather than indistinguishable from success.
- A fenced (superseded) controller now releases GPU leases and cancels in-flight GPU arbiter tasks for its workers, matching the sibling termination branches. Previously it only marked workers offline and skipped the lease release and arbiter cancellation, stranding VRAM leases and allowing arbiter tasks to collide with the winning controller.
- Deploy wizard no longer lets the user reach the incoherent memory pair (skipped layer + `both`/`taosmd` mode) that triggered a 400 at the end of the wizard: clicking "Skip memory for this agent" now snaps the mode to `framework`, and the `both`/`taosmd` mode buttons are disabled with a "needs the taOSmd memory layer" tooltip while the layer is skipped. The same guard is mirrored in the agent Settings memory tab, which now sends `memory_mode: framework` when switching the plugin off. The server-side validation guard from #2405 remains in place.
- Folded the five #2422-verified sha256 + download_url pairs into the merged Hailo HEF a8w4 variants across llama-3.2-3b, qwen2-1.5b, qwen2.5-1.5b, qwen2.5-coder-1.5b, and deepseek-r1-1.5b; removed the install.method: hailo-ollama-pull carve-out from the integrity test so every variant now carries a pinned sha256 + https download_url; variant-level context_window 2048 declared on all hef builds so NPU context no longer inherits the model-level 131072/40960/32768 values; hailo-ollama install path now targets the hailo daemon on :7836 instead of Ollama's :11434.
- Ollama and hailo-ollama installs targeted at a remote worker (`target_remote`) now pull models onto that worker's daemon instead of the controller's localhost; `resolve_ollama_url(target_remote, backend_id)` selects the correct host and port (11434 for ollama, 7836 for hailo-ollama via `TAOS_HAILO_OLLAMA_PORT`) following the same convention as `resolve_rkllama_url`.
- paligemma-2 manifest: switch from single-shard `download_url` to `hf_repo` + `multi_file: true` so the installer fetches all shards; added combined-hash verification to `HFMultiInstaller` and a sweep-test guard that flags any sharded `download_url` missing the multi-file marker.
- deleted-symbols CI gate no longer reports false positives on `pull_request` re-runs after the base advances: the merge result is recomputed in-script via `git merge-tree --write-tree <base> <pr-head>` (`scripts/check_deleted_symbols.py`) instead of comparing the event-time test-merge commit checked out as HEAD
- Llama 3.2 3B Instruct HEF model sha256 corrected in manifest after verification against upstream download
- Disable option buttons and Submit button while a POST is in flight, preventing duplicate submissions that cause 409 errors
- Clear answerError at the start of each new submission attempt
- Distinguish refresh-failure from submit-failure: when POST succeeds but follow-up GET fails, do not show "Failed to answer"
- On 409 (someone else answered first), refetch the decision so the block flips to its answered state
- Reset answer and answerError state when block.decision_id changes
- Split the single large routes doc into compiled per-area fragments in `docs/routes.d/`, with a deterministic compiler script `scripts/build-routes-doc.py`. Resolves merge-conflict collisions when multiple lanes touch routes.
- Removed the `install.method: hailo-ollama-pull` carve-out from the model manifest integrity test; every variant (including HEF/hailo-ollama) must now carry a 64-char lowercase hex sha256 and a non-empty https download_url. The `_is_stride2_algorithmic` detector that supported the deleted carve-out has been removed.
- Distrust green gate: CI check now fails PRs where added or modified test files have all tests skipping via `pytest.importorskip` or `pytest.skip`, with an escape hatch for intentional landing tests (`Tests-Skipped-Intentionally` trailer in PR body).
- Restore error propagation from `answerDecision` so non-409 server failures (500, network errors, 4xx) surface the server-provided reason in the alert region instead of being swallowed
- Surface a fallback message when the post-409 refetch itself fails, rather than leaving the block pending with no feedback
- The `bot-review-gate` workflow no longer crashes on `issue_comment` events: the `bot-review-gate` job is guarded to run only on `pull_request` and `pull_request_review` events (where `github.event.pull_request.number` resolves), and a new `re-run-on-stub-comment` job re-runs the gate for the PR head SHA when a CodeRabbit rate-limit stub comment lands after the initial green run. Inert `branches` filters on `pull_request_review` and `issue_comment` triggers have been removed.
- `POST /api/notifications` now rejects unknown `level` values with a 400 error, matching the canonical level set `{"info", "success", "warning", "error"}` (single source of truth: `VALID_LEVELS` in `tinyagentos/notifications.py`, shared with the `notify_user` tool).
- `check_all_skip.py` now reports zero-collected violations in the final `::error` annotation alongside all-skip violations, instead of incorrectly claiming "0 file(s) have all tests skipping" when only zero-collected files are present.
- paligemma-2: pin hf_revision to immutable commit, replace metadata sha256 with file_set_hash for multi-file install verification
- POST /api/models/download: route multi_file variants through HFMultiInstaller instead of the single-file download path
- The core-dep integrity guard diagnostic in tests/conftest.py now prints `name==version` for each reported module (instead of bare names), plus resolved `__file__` and whether `__spec__.submodule_search_locations` is set -- the two observations that discriminate a stale/partial install from a version-bump API removal. The error text states the observation and names both candidate causes rather than asserting a stale install as fact. Installed-package lines now include versions.
- CodeRabbit login filter now includes `coderabbitai[bot]` so that `collect_coderabbit_items` correctly identifies CodeRabbit output on PRs where CodeRabbit posts as `coderabbitai[bot]` (instead of only matching `coderabbit[bot]` and `coderabbitai`)
- **Dropped the hardcoded mute on `task.claimed` notifications.** The per-type
  toggle preferences remain, but no event type is silenced by default. A user
  who has never opened the Notifications pane now receives every event type,
  including `task.claimed`. A regression test asserts delivery of an
  unmodified-user `task.claimed` notification, so re-introducing a silent
  default mute will fail CI.
- DecisionsApp `load()` now guards each state update with a monotonically increasing request sequence, so a stale in-flight response can no longer overwrite newer data when mount, focus refresh, or SSE-driven reloads overlap
- `POST /api/cluster/workers` now returns `409 Conflict` when the controller is fenced or the worker echoes a stale generation, instead of incorrectly replying `200 registered` while leaving the worker absent from the registry. This stops superseded controllers from misleading workers into heartbeating against a controller that has no record of them (#tsk-yl23ua).
- `check_all_skip.py` now treats files with 0 collected outcomes but >0 AST-defined tests as a violation, instead of silently passing the gate.

### Removed

- `notes_set_done` agent tool superseded by the richer todo tools above (#2035).

## [1.0.0-beta.49] - 2026-08-15

### Added

- Fixed `PUT /api/config` silently dropping `archive`, `archived_agents` and `github_app_id`: both `AppConfig` rebuild sites (config save and backup restore) omitted them, so saving settings wiped an archive target, the archived-agent list and the GitHub App id. A key-parity test now fails if any `to_dict()` field is forgotten at a rebuild site.
- Added a Lists tab to the Projects app: a rail of the project's lists beside an entry panel with quick-add, done toggles, category and status pills, a status selector, and the original text behind any entry an agent tidied.
- Fixed memory settings, catalog indexing and per-agent memory-config updates failing with a 403 "CSRF token missing" on a cookie-authenticated session: the three mutating calls in the Memory API client did not send the double-submit token.
- Fixed three ways `GET /api/a2a/bus/messages` returned HTTP 200 and nothing, leaving a reader silently disconnected: `channel=all` (the idiom the raw bus and `taosmd a2a-watch` document for "every thread") was forwarded as a channel literally named `all` and matched nothing; an unknown channel name was indistinguishable from a quiet one; and unrecognised cursor params such as `since_id` were silently dropped, so an incremental reader re-read the whole window every poll believing it held a cursor. `all` and `*` now read every thread, an unrecognised query param is a 400 naming the accepted set, an empty result for a named channel reports `channel_known`, and `thread` is accepted as an alias for `channel`.
- **Doc-drift gate covers the full doc surface**: the invariants scan now
  takes globs and checks every agent-manual page, runbook, OS skill
  (`.claude/skills/*/SKILL.md`), the worker README, CONTRIBUTING and
  RELEASING for references to files that no longer exist (with a documented
  tombstone list for deliberate mentions of removed files). Three new
  diff-gate rules: desktop-driving route changes require the taos-agent
  skill / OS-control manual reviewed, update/release machinery changes
  require RELEASING.md or a runbook reviewed, and worker-tree changes
  require the worker README. RELEASING.md now documents the sync-branch
  promotion pattern for a BEHIND dev->master PR, including the back-merge
  and empty-tree-diff identity check.
- Fixed `POST /api/agents/registry/mint-internal` and `seed-internal` forking a duplicate identity for every driver agent that had self-joined through the consent flow. The consent approve path stores a slugified handle (`@taOSmd-dev` becomes `taosmd-dev`) while the internal-driver table names the display spelling, and the lookup was an exact SQL match — so the mint missed the existing row and registered a second one, which then received the driver scopes and a token while the original identity kept its project grants. The lookup now falls back to the slugified handle.
- **LoRA Studio backend**: share a Civitai LoRA/LoCon/DoRA model URL and taOS
  archives it -- safetensors file (SHA256-verified), name, description,
  preview images, tags, and trigger words -- under a new `loras` store and
  `/api/loras/*` endpoints. Civitai's edge geo-blocks some regions with HTTP
  451; a new `lora_ingest_proxy_url` config key lets the fetcher (and only
  the fetcher) go out through an explicit proxy instead. Every failure mode
  (451, connect error, SHA256 mismatch, a non-LoRA model type) fails loud
  with a specific reason and leaves no partial file on disk. LoRA files live
  under `models_root()/loras/` and are excluded from the Models app's disk
  scan so adapters never show up as loadable models. `/api/library/ingest`
  also recognises Civitai URLs and delegates to the same ingest job.
- **The OS-native agent has its own identity.** Every install now mints an agent identity at first boot — no admin step and no prompt. Previously the built-in agent authenticated as the owner (the browser session or the admin-equivalent `.auth_local_token`), so its actions were indistinguishable from the human's in every audit trail, it could not appear on the A2A bus as itself, and nothing it did could be revoked without revoking the human. The identity is per-install (anchored to `.install_id`), owner-linked, and conservative: `a2a_send` + `a2a_receive` only, with anything further going through the existing user-mediated scope-request flow. Its token is written to `<data_dir>/.taos_agent_token` (0600) and never leaves the install that minted it. The identity is provisioned but not yet wired into the chat runtime, which still authenticates as the owner as before; this ships the identity, not the switchover. Registry rows gain an `install_id` column so an owner's identities can be listed and revoked per machine.
- Design spec for taOS Beach, the sandbox provisioning system: object model, state machine, approval flow over the Decisions app, quotas, port and DNS hygiene, harness-agnostic agent access, and a Phase 1 cut with acceptance criteria (`docs/design/taos-beach.md`).
- **Shared `useRefreshOnFocus` hook + adoption in seven high-traffic apps.** A new `desktop/src/hooks/use-refresh-on-focus.ts` hook re-runs a supplied refetch callback when the window regains focus or the document visibility state returns to visible, with a ~1s debounce that coalesces rapid focus flapping. It is now wired into Projects, Agents, Messages, Files, Notifications, Cluster, and Decisions so windows show current data without requiring the user to close and reopen them.
- **Settings-update brings a locally-hosted taOSmd to latest in the same
  action**: with the new config keys `taosmd_dir` and `taosmd_restart_cmd` set
  (and `memory_url` local), `POST /api/settings/update` ff-only-pulls the
  taOSmd checkout, announces the restart on the A2A bus `build` thread before
  dropping SSE subscribers, restarts the service, and then verifies the
  RUNNING server's `/health` — Content-Type must be `application/json` (a
  `text/html` 200 from the SPA catch-all fails) and the core capability
  identifiers (`a2a.v1`, `collections.v1`, `search.v1`) must be present in the
  body. Any taOSmd failure fails the whole update loudly with a named reason;
  unconfigured or remote installs get an explicit `taosmd: {"skipped": <why>}`
  in the response, never a silent half-update (tsk-jjkukj).
- **OS-level typed change-event stream + `useOsEvents` hook.** A new authenticated SSE endpoint (`GET /api/os/events`) streams typed change events carrying only the event kind, id and timestamp, never the payload, so apps can opt into live updates with a single hook call. The shared `useOsEvents(kinds, onEvent)` hook manages one connection per client, exposes `connected` and `stale`, reconnects with exponential backoff, and reopens the stream when the requested kinds change. At most 256 events are buffered per connection: a client that falls further behind loses the oldest and is told so with an `events.lagged` frame rather than silently stalling. The lag frame is a control frame, so it reaches subscribers that asked for a narrow set of kinds, and repeated lag frames are never collapsed as duplicates. Requested kinds are filtered as events enter that buffer rather than as they leave it, so unrelated traffic cannot evict the events a subscriber asked for; and a `kinds` parameter that names no kind (`?kinds=`, `?kinds=%20`) means every kind, where it previously matched nothing and delivered an empty stream.
- Added the project lists HTTP API: `/api/projects/{pid}/lists` and `.../lists/{lid}/entries` (create, read, update, delete and reorder), usable by a project owner/admin session or by an agent holding the new project-bound `project_lists` scope. A token without the scope is refused 403; a token bound to another project gets 404 so it cannot confirm that project exists. A reorder body must name both `id` and `position` for every element (422 otherwise), and a reorder that matches no entry returns 400 without logging a reorder to the project activity feed.
- **ModelsApp refresh-failure guard.** A background refetch that hits the total-failure path no longer blanks real, already-loaded models with the "No models yet" empty state. When `downloaded.length > 0` the failing refresh now leaves the rows on screen and only clears loading, while the no-data path still shows the empty state as before. `useRefreshOnFocus(fetchModels)` is also wired in so the guard is exercised on every window focus.
- Adopted `useRefreshOnFocus` in Tasks, Activity, Models, and Notes so each window refetches its current data on focus without requiring a reopen.
- Fixed Routines (Tasks) blanking to "No scheduled routines" when a background refresh hits an unreachable backend; the routines already on screen are kept instead.

### Fixed

- **Un-quarantined cards return to a genuinely claimable pool**:
  `unquarantine_task` set the card back to `open` but kept the old
  `claimed_by`, and `claim_task` requires an unclaimed row -- so a
  claimed-then-quarantined card came back permanently unclaimable.
  Un-quarantine now clears the claimer, matching `reopen_task` and
  `release_task`. The generic `update_task` edit path (owner/admin PATCH)
  had the same gap when setting a claimed card's status back to `open`;
  it now clears the claimer too.

### Added

- Complementary memory mode: each agent now has a `memory_mode` field with three
  values (`both` default, `framework`, `taosmd`), surfaced in the Agents app deploy
  wizard and persisted on the agent record. The mode is injected as `TAOS_MEMORY_MODE`
  at deploy time so the framework runtime can honour it without a separate config push.

- Three onboarding guides in `docs/agent-manual/` covering each memory mode:
  `12-memory-mode-both.md`, `13-memory-mode-framework.md`,
  `14-memory-mode-taosmd.md`. The compiled agent manual stays under the size limit.

### Changed

- Doc-gate now triggers on plain modifications (not only add/delete) for
  behaviour-bearing trees: routes, installers, app-catalog, and auth_middleware.
  A modified route file now requires `docs/agent-coordination.md` to be touched
  in the same PR.

- A new CHANGELOG rule covers every code change under `tinyagentos/` or
  `desktop/src/` that is not test-only: such changes require a `CHANGELOG.md`
  edit or a new `changelog.d/` fragment.

- Agent-facing coverage is broadened: changes to the agent identity and scope
  surface (`agent_scope_requests_store.py`, `agent_auth_requests.py`, and
  related token-auth files) now require `docs/agent-manual/` to be touched.

- A shared `useRefreshOnFocus` hook re-runs a supplied refetch when the
  desktop window regains focus or document visibility returns to visible,
  debouncing within ~1s to coalesce focus flapping. It is adopted by Projects,
  Agents, Messages, Files, Notifications, Cluster, and Decisions.

### Changed

- The Docs-Reviewed trailer override is now logged in CI output: when a commit
  carries the trailer, the gate prints the commit hash, author, and trailer
  text. The escape hatch still works exactly as before.

## [1.0.0-beta.48] - 2026-08-11

### Added

- Quarantined task cards surface their strike count and latest strike on the
  task-detail response, and a lead can un-quarantine a card via
  `POST /api/projects/{pid}/tasks/{tid}/unquarantine`, clearing its strikes (#2333).

- **Hailo-10H HEF model catalog**: five NPU-accelerated model manifests
  (DeepSeek-R1-Distill-Qwen 1.5B, Llama 3.2 3B, Qwen2 1.5B, Qwen2.5 1.5B,
  Qwen2.5 Coder 1.5B) now resolve and install via `hailo-ollama` on
  Raspberry Pi 5 + AI HAT+2, and downloaded `.hef` files show up in the
  local-files and orphan scans (#2338).

- The Agents app registry panel shows each agent's handle (alias) and lets the
  owner or an admin edit it inline, saved via
  `PATCH /api/agents/registry/{canonical_id}`. A leading `@` is display syntax
  and is stripped before save (#2349).

- Wallpaper fit options in Settings -> Desktop & Dock: fill, fit, stretch,
  center, and tile. The choice is persisted per device (localStorage, keyed
  by a locally minted device id that is never sent to the server), so each
  screen keeps the fit that suits its aspect ratio (#2357).

- **Agent loop infrastructure**: new `tinyagentos.agent_loop.AgentLoop` library
  for subagent delegation and safe-point message queuing. Landed as
  standalone infrastructure; wired into the chat router and taOS agent
  routes in #tsk-icpt4i (#tsk-rl2lfb).

- **CI**: store-wiring-gate workflow and `scripts/check_store_wiring.py` guard.
  A PR that adds a new BaseStore subclass without wiring it into
  `tinyagentos/app.py` now fails CI and names the unreachable class and file.
  Routes reach stores ONLY via `request.app.state`, so an unwired store is
  dead code. Only newly added classes are policed; a
  `Store-Unwired-Intentionally: <ClassName>, <why>` trailer in the PR body
  waives a named class for stores genuinely constructed elsewhere (tsk-n3w5mh).

- **Docs**: mechanical-simple-auditable design law added to the agent manual
  (`01-rules.md`), with a worked example anonymised as "an agent". Also trimmed
  verbose prose in the image-prompting guide to stay within the compiled manual
  character budget.

### Changed

- CI's `spa-build` job runs on Node 22 (was 20, now past end-of-life). Also
  unblocks the jsdom 30 upgrade, which requires Node >= 22.13 (#2353).

- **Agent loop wiring**: `AgentLoop` is now the single per-agent serialization
  owner. `AgentChatRouter` drives OpenClaw ACP turns through a per-agent
  `AgentLoop` (replacing the per-agent lock) and the turn-holder drives
  messages queued mid-turn at its safe point. The desktop taOS agent chat
  endpoint serializes on one `AgentLoop` too -- fixing a race where two
  concurrent POSTs shared the opencode session with no serialization --
  queueing concurrent messages and surfacing them in the turn-holder's stream
  tail. New `GET /api/taos-agent/status` endpoint returns the desktop loop's
  status scoped to state / current turn / queue depth / subagent descriptors
  (subagent result/error payloads stay server-side) (#tsk-icpt4i).

### Fixed

- Project list entries: `get_entry` no longer reads cursor metadata after the
  cursor closes, and a failed reorder now rolls back its partial updates so a
  later unrelated write cannot commit a half-applied ordering (tsk-u23vjy,
  fix-forward of #2183).

- The Agent-as-a-Model surface (`GET /v1/models`, `POST /v1/chat/completions`)
  is now reachable by external OpenAI-compatible clients: the auth middleware
  passes exactly those two routes through to their own consent-key check
  instead of rejecting every session-less caller before the handler ran. All
  other `/v1` paths remain session-gated (tsk-hfs6zv).

- **PWA refresh loop after a browser auto-update**: `/auth/status`, `/auth/me`
  and the chat/canvas/terminal/web-chat WebSocket handlers now apply the same
  session User-Agent binding check as the API middleware. Previously a session
  created before a browser update kept reading as authenticated on
  `/auth/status` while every `/api/*` call was rejected, so the desktop shell
  remounted in a loop; the WebSocket endpoints conversely accepted a cookie
  the APIs refused.

- **Catalog manifests' `context_window` was silently dropped**: `AppManifest`
  declared no `context_window` field and `from_dict` never read the YAML
  value, so every manifest loaded as 0 and the chat context-window budget code
  always fell back to the 4000-token "unknown window" default. The field now
  loads onto `AppManifest` (0 reserved for unknown), so real windows -- e.g.
  rkllm 4096, qwen 32768 -- drive the #1740 budget math. (#2338, #1740)

### Security

- Memory routes reject any `agent` value that is not a single plain path
  component (separators, `.`/`..`, NUL all 400): the caller-controlled name
  becomes a filesystem path component of the qmd `dbPath`, and a traversal
  value could previously address SQLite files outside `agent-memory/` (#2352).

- All owner-gated agent-registry routes are now existence-hiding: a caller who
  does not own an agent gets the same 404 as a nonexistent id, on the
  scope-request create/approve/deny routes and on registry PATCH, revoke,
  rotate-tokens, and org update. Previously a 403-vs-404 difference disclosed
  whether an agent id existed (issue #2106, reported by hognek) (#2356).

- **CI**: `secret-ignores-gate` workflow and `scripts/check_secret_ignores.py` now
  assert, on push to `master`/`dev`/`release/*` and on PRs to those branches, that the
  committed `.gitignore` still contains every secret-protection rule (`*.key`,
  `*.p8`, `identity.json`, `*credentials.json`, `*creds*.json`, the `*_private.*` key
  shapes, `secrets/`, `data/hub/`, and more) and that known secret-shaped paths
  are all reported ignored by `git check-ignore`. Closes the "promotion must be
  verified, not assumed" gap from #2171/#2173. Removing any one pattern is
  proven to fail the gate by a parametrized test (tsk-laezfg).

- **Desktop deps**: bump `dompurify` 3.4.12 -> 3.4.13 (GHSA-55q2-fjhq-7xh7,
  moderate) and `nanoid` 5.1.11 -> 5.1.16 (CVE-2026-67214, high) in
  `desktop/package-lock.json`; lock-only, both already within the declared
  ranges. Split out of Dependabot #2331, whose grouped jsdom 30 bump fails
  spa-build (jsdom 30 requires Node >=22.13; CI pins Node 20).

## [1.0.0-beta.47] - 2026-08-09

### Added

- **Devices**: pairing requests can be created and tracked via the new device
  pair-request API, with approval or denial surfaced to the user through the
  Decisions app (#2233).

- **Docs**: new `taos-agent` OS skill (`.claude/skills/taos-agent/SKILL.md`) that
  consolidates the agent-manual OS-operation content into actionable instructions for
  the OS-native agent (opening and driving apps/windows, projects, files, memory,
  notes, chat conventions, image generation, and answering the user), with the hard
  rule that all desktop driving goes only through `POST /api/desktop/command` +
  `POST /api/desktop/screenshot` prominently featured. The agent-manual index now
  points at both the OS skill and the existing `taos-development-skill`. Draft for
  @taOS-dev review.
- Projects gain a Notes area: title + markdown notes per project, readable and
  writable by the project owner or by an agent holding a project-bound
  `project_notes` grant (new requestable scope) (#2285).
- Chat renders `text` and `thinking` content blocks: thinking is a
  collapsed-by-default disclosure with a proper ARIA expand/collapse contract (#2282).
- Messages sidebar shows a live "thinking" badge on channels whose bound
  taOStalk agent is currently working, on desktop and mobile (#2281).
- Admin-only `POST /api/notifications` so orchestrators and lead agents can
  raise review-request notifications through the store (and therefore through
  SSE and web push) instead of a raw database insert (#2280).
- Chat renders `tool_call` and `status` content blocks: tool calls as a
  collapsible detail with an ARIA disclosure contract, status (and the
  `question` variant) as a muted line with a "reply below" hint (#2275).

### Changed

- Contributors add a `changelog.d/<pr>-<slug>.md` fragment instead of editing
  `CHANGELOG.md`, so concurrent PRs no longer conflict on the shared
  `[Unreleased]` anchor; `scripts/collate_changelog.py` folds fragments into a
  release section at bump time. Editing `CHANGELOG.md` directly still works.
- The Tasks app is now called Routines in the launcher and window title. The
  app id is unchanged, so existing layouts and pinned positions are preserved
  (#2298).
- Approving an agent auth-request with `defer_binding` now returns 409 when that
  agent already has an active handle, and the response points the operator at
  `POST /api/projects/{project_id}/members/assign-agent`. It previously advised
  minting a second identity, which splits an agent's memory and grants across
  two canonical ids (#2313).

### Security

- Bumped `cryptography` from 48.0.1 to 50.0.0, picking up the upstream fixes
  for PYSEC-2026-3552/3553/3554 and CVE-2026-69247 (PKCS#7 EnvelopedData
  Bleichenbacher). Also updates `uvicorn[standard]` to 0.52.1.

## [1.0.0-beta.46] - 2026-08-03

### Fixed

- Decisions API: authentication now runs before request-body validation, so an
  invalid bearer token always returns 401 and token validity can no longer be
  probed through validation errors (#2268).

### Added

- **Library**: settings pane for download preferences: preferred quality and per-source rules (#2276).

- Observatory fleet view for agents holding a global `observatory_control`
  grant; project-scoped grants see only their granted projects (#2267).
- Deployed agents are registered into the agent registry at deploy time, each
  minting its own canonical identity; names that resolve to a reserved prefix
  are rejected with a 400 (#2266).
- Lead agents can edit their own board cards: the seeded internal lead now
  carries the `project_tasks_update` scope (title/body/labels/priority on
  own-or-lead cards; a plain `project_tasks` grant still gets 403 on PATCH,
  pinned by a regression test) (#2244).
- **Doc-review stamps reconciled into dev** (#1835 / #2247): the review-state
  store, routes, `project_doc_review` agent scope (now requestable via the
  consent flow and internal mint) and the Files-app review UI. The feature has
  shipped on every install since beta.43 but lived only on the release branch;
  it is now developed and reviewed like everything else.
- **Docs**: the agent manual now documents the project Files REST API for member
  agents (multipart upload, listing, fetch, and the one-write principle), linked
  from the manual index; the compiled-manual size guard is raised to 18000 chars
  to make room (redo of #2139).

## [1.0.0-beta.45] - 2026-08-02

### Added

- **Community view**: collaborator stats, leaderboard, and a read-only kanban of
  the public board (#2042).
- **Notes/Todo split**: Notes and Todo are now separate apps, final integration
  pass (#2033).
- **Hub sealed-envelope relay**: X25519 store-and-forward through taos.my so two
  boxes can exchange DMs without a direct connection (#2034), plus the E1 DM
  schema migration preparing cross-box messaging (#2047).
- **Library**: storage accounting view (#2099), YouTube and Web ingest processors
  with streaming, content-type gates and timeout guards (#2177), a broken
  thumbnail now shows a placeholder instead of blank space (#2120), and the
  source ingest option is actually wired up (#2117).
- **Memory app**: shows taOSmd running mode, reachability and tier, with a
  switch-to-remote control (#1959).
- **Wallpaper picker**: Wallhaven browse via a server-side proxy, sectioned
  picker (#1902).
- **Decisions**: agents can ask a Decision and mirror a chat answer back onto the
  card, with spoofing, consent and cross-project protections layered on
  (#2179 series).
- **Scope requests in the bell**: approve/deny buttons directly on scope-request
  notifications (#2107), and `project_tasks_update` so lead agents can edit
  their own board cards (#2184).
- **taOStalk groundwork**: typed `content_blocks` and the render dispatcher
  (#2154), theme-token migration (#2151).
- **Share destinations**: authorization-filtered `GET /api/share/destinations`
  (S2A, #2146).
- **App tiering S1**: registry tier/group/handler fields (#2185).
- **Device-bearer self-service**: push-token rotation and device management on
  the device token itself (#2232).
- **Reserved agent names**: `user-`, `human-`, `admin-` and `taos-` prefixes are
  rejected at registration (including punctuation/spacing obfuscations), so an
  external agent cannot squat an identity that reads as a person or as an
  internal taOS agent (#2237).
- Per-user 24h feedback submission cap (#2131).

### Fixed

- **Push notifications never routed to the correct app.** Three independent faults
  in the service-worker click path meant tapping any notification either opened
  the desktop root or did nothing: the backend dropped routing fields before the
  SW saw them, the deep-link fallback was always root, and no shell listener
  existed for the common mobile-PWA case where the app is already open. Decision
  pushes now open Decisions (or the mapped target app) whether or not a window
  is already open (#2179).
- **Web push was silently broken**: the VAPID key format made every send fail
  (#2166).
- **Wallpapers squashed on square and odd-ratio screens**, in both desktop and
  browser modes.
- **SPA entry points missing the auth guard**: every entry point now installs it
  (#2174).
- **App Store install gate hardening**: signing failure is fail-closed instead of
  silently unsigned (#2050), date-safe manifest canonicalisation and an async
  TOCTOU re-check, and the unsigned-manifest policy contradiction is resolved
  (#2218).
- **Scope binding**: `project_tasks_create` and file scopes bind to a project on
  approval instead of floating globally (#2127).
- Watch-face projection issues (#2230).
- CSRF token is merged into `Request`-object fetch inputs too (#1999).
- Canvas `.tldr` export produces a file tldraw can actually open (#2133).
- GPU arbiter: `drain_tick_seconds` floor clamped and a double capacity wake
  removed (#1987).
- Project create enforces name uniqueness and auto-rejects duplicates (#2168).
- Duplicate channel header on rebased share routes (#2169).
- Project lists: entry positioning and missing store wiring.

### Changed

- **CI test suite is sharded** across parallel jobs (#2137), the dependency audit
  runs on dev PRs (#2189), the test timeout sits above the real suite runtime
  (#2134), and CodeRabbit no longer spends review quota on generated diffs
  (#2136).
- **Dev dependencies moved to `[dependency-groups]`** so a plain `uv sync` gives
  a working test environment (#2217).
- **Doc drift gate**: rules can opt into firing on plain modifications
  (`on_modify`), with changelog, agent-manual and contributor-skill coverage
  (#2236).
- The Workspace tab no longer embeds a chat pane.
- Security dependency bumps: pillow 12.3.0, dompurify 3.4.12.
- Docs: verification and collision working rules written down (#2163), realtime
  A2A connection guide for deployed agents (#2161), agent token storage
  hardening (#2159).

## [1.0.0-beta.44] - 2026-07-26

### Added

- **Cluster**: device and node revoke and blocking controls (#2238).

- **Assistant Studio**: a workspace app for a personal-assistant agent. Pick a
  registered agent as your PA, then work out of one hub with Overview, Journal,
  Calendar/time, Tasks, Comms, Canvas, and a Deliverables area (#2103, #2104).
- **Agent project-file access**: the `files_read` / `files_write` scopes are now
  enforced on the project-files routes, and the invite bundle surfaces the Files
  API, so a member agent can read and add project files (#2100).
- **Nous Portal** as a first-class cloud model provider (#2102).
- **Scope requests**: an existing agent (or its owner/admin) can request
  additional scope grants on the same identity, owner/admin approved (#1921).
- **`project_tasks_create` scope**, so an external agent can author board cards.
  Deliberately separate from `project_tasks`, which stays read plus lifecycle
  plus comments, so an existing approval keeps meaning what it meant when it was
  given (#2098).
- Library item-card component with thumbnail, status, and artifacts (#2097).

### Fixed

- **Dialogs rendered behind windows, and minting an invite never showed the URL
  and PIN.** The window z-index counter grew unbounded until it passed the
  overlay layer, and the mint dialog unmounted before it could render the
  result (#2092).
- **Agent terminal/TUI shortcuts failed with "Instance not found."** The PTY
  path opened `incus exec` with no `--project`, so it used the client default
  project and could not reach a container in another project (e.g. a legacy one
  in `default`). It now resolves the container's real project and starts it if
  stopped (#2105).
- Scope-request approval security fixes: global-capable scopes no longer bind to
  an agent-supplied `project_id`, approval/deny take the per-request lock, and
  create authorizes before scope-vocabulary validation (#1921).

## [1.0.0-beta.43] - 2026-07-21

### Added

- **Library app P1**: LibraryStore, ingest pipeline, cheap-tier processors (file,
  text, PDF, image) and the collections handoff to taOSmd. Ingested items are
  copied into a per-item directory and registered as a taOSmd collection over the
  live Collections API, with async index polling and typed link rows (#2062).
- Invite mint accepts an optional `ttl_secs` (60s to 24h) so a longer-lived
  invite is a deliberate choice rather than a code change (#2072).

### Fixed

- **Invite dialog crashed the desktop.** The invite list endpoints returned
  `scopes` as a JSON-encoded string while the UI typed it as an array, so the
  dialog threw and tripped the SPA error boundary whenever any pending invite
  existed. This also caused the post-mint refresh to unmount the dialog before
  the URL and PIN were shown (#2066).
- **Expired invites could not be revoked.** `revoke` only matched `pending`, so an
  invite that lazily flipped to `expired` returned 404 while still listed, leaving
  dead rows against the pending cap. Revoke now covers expired, and terminal
  states return 409 with the actual state (#2071).
- Default invite TTL raised from 15 minutes to 1 hour. Handing a URL and PIN to a
  human who then configures an agent is not a 15 minute flow (#2072).

### Changed

- `docs/getting-started.md` documents the Hailo-10H AI HAT+2 and the Raspberry Pi
  5 M.2 slot conflict: the HAT occupies the only M.2 slot, so it cannot be used
  alongside an NVMe boot drive (#2075).
- Contributor docs gained seven new defect classes drawn from real review
  findings, covering runtime state in commits, mobile view registries, retrofit
  migrations on shipped stores, scope honesty, tolerance assertions, shell
  snippets in template literals, conflict resolution, and how an external
  contributor reaches another team's agent (#2069, #2079).

## [1.0.0-beta.42] - 2026-07-20

### Added
- GitHub App installation flow with per-agent GitHub token grants: install the App, grant repos to agents, short-lived tokens minted per installation, RSA key stored encrypted in Secrets (#1932, #2036, #2009, #1997)
- Cross-user collaboration foundations: contacts store with signed-envelope peer channel (A1), human project membership with collab invite kind and two-sided consent (B1) (#2025, #2045)
- Todo app backend: TodoStore with ordering and due dates, list-to-Todo migration with idempotent endpoint, whitespace validation (#1944, #2028, #2049)
- Worker self-update foundations: WorkerUpdateService version polling and graceful pause + drain protocol (#1907, #1903)
- Mesh: guest peer nodes surfaced in mesh_status with guest-preauth proxy endpoint (#2038)
- taOSmd memory URL connection-test and reachability reporting in Settings (#1931)
- Agent kill-switch: Ctrl+Shift+K shortcut with SIGTERM to SIGKILL grace window (#1962)
- Registry hygiene: revoked/rejected/suspended entries collapsed by default; @taOS-dev granted board scopes (#2004, #2022)
- 109+ new frontend component tests across chat and desktop (#2051, #2052, #2053, #2054)
- Design specs merged: Library app universal ingestion, taOStalk slice 1 session bridge, cross-user collaboration epic (#2056, #2029, #2011)
- Contributor docs: recurring review pitfalls checklist and PR lifecycle discipline in the development skill (#2040, #2055)

### Fixed
- Security: store-signing hardening (5 findings: 422 on unknown backend, narrow excepts, perms), GPG fingerprint resolution uses the primary key across all VALIDSIG shapes, invite no longer burned on failed approve with scope validation at mint (#2023, #1983, #2002)
- GitHub App key semantic conflict between two green PRs resolved: app key read from SecretsStore everywhere (#2041)
- SQLite stores: WAL mode enabled and sync-in-async fixed (#1905)
- Agents: removed task.cancel() that defeated asyncio.shield in kill-switch handlers (#1988)
- Catalog: standardized install scripts for Hermes/OpenClaw/DeerFlow, corrected qwen2.5 rkllm context window (#1934, #2008)
- Desktop: MessageList findings, wallpaper bot-fix v2, port allocation centralized for userspace deploys (#1877, #1982, #1990)
- Framework registry: retired alpha verification_status, dead-code cleanup from the Fable audit (#1995, #2003)

### Changed
- Dependencies: setup-node 4 to 7, desktop spa-deps group (12 packages) (#2030, #2031)

## [1.0.0-beta.41] - 2026-07-18

### Added
- External agents can be onboarded end to end: invite one to a project (or with no project and a chosen name/alias) as a URL plus PIN, the agent redeems it and receives an onboarding kit, you approve the request, and a project lead can mark board cards claimable so an invited agent knows what to pick up (#1858, #1867, #1918, #1971, #1975, #1976).
- One agent identity can now hold per-project grants behind a grant-gated token, so the same agent can work across several projects without a shared credential (#1866).
- The GPU arbiter is consolidated onto a single VRAM authority with eviction and heartbeat fixes, and workers can be rolling-updated one at a time with a drain step so a cluster update does not take everything down at once (#1859, #1878).
- The taOSmd memory URL is configurable, and the deploy wizard now shows a framework's verification tier (tested, beta, experimental) so you can prefer the more-verified ones (#1904, #1963).
- Desktop: a categorized wallpaper picker with a theme default in Settings, editing the last assistant message before resending, a release-channel selector in the Updates panel, automatic SPA reload when a new build is deployed, and off-screen windows clamped back into view on resize (#1882, #1886, #1906, #1933, #1874).
- Phone web-push notifications are scoped per user ahead of multi-user (#1885).

### Fixed
- Creating a project, approving an agent access request, and other mutating actions no longer fail with "CSRF token missing"; the websocket path is also exempt so Messages/chat no longer 500s offline (#1969, #1977, #1898, #1922).
- Security: torrent SHA256 verification is mandatory, installer downloads are pinned to a validated public IP to close a DNS-rebinding SSRF, auth gained XSS and session-race hardening, and the invite advert page HTML-escapes the project name (#1901, #1879, #1925, #1919).
- Stores: an audit and regression gate ensures no SCHEMA index references a migration-added column (the recurring upgrade brick), and a scheduling lease renewal TOCTOU is closed (#1960, #1900).
- The verified framework list returns tested and beta frameworks, not beta only, so the most-verified framework is no longer excluded (#1978).
- qmd calls gained retry jitter and a per-call timeout override, and opencode turns time out instead of hanging the HTTP request (#1961, #1909).
- Notification archiving persists to the backend so it survives a reload (#1917).

## [1.0.0-beta.40] - 2026-07-11

### Added
- Approving an external agent for the project-tasks scope now asks which project to bind it to, with an inline option to create a new one, and the approval adds the agent as a member of that project so it shows up in the project's Members and joins the project channel. Granting project-tasks without picking a project is refused, so an agent's own request can never bind it to a project you did not choose (#1777).
- taOS notifications can now reach your phone as native web-push. Install the PWA and allow notifications, and access requests and other alerts arrive as OS banners even when the app is closed, delivered best effort so a push failure never blocks the in-app feed (#1778).

### Fixed
- Agent chat now sizes its history budget to the model's real context window instead of a fixed limit, so a small-context local model no longer overflows and loops. When several agents share one reply the budget follows the smallest known window, and any unknown window keeps the previous safe default (#1740, #1779).

## [1.0.0-beta.39] - 2026-07-10

### Added
- Game Studio can now generate textures and sprites from a text prompt using a ComfyUI backend on a discrete-GPU worker, writing the image straight into the game's file set. On a host with no capable GPU the panel shows a clear "needs a GPU worker" state instead of failing (#1773).
- taOSgo cluster-join now completes the network side: a controller joins the account mesh over the system tailscale against the Headscale server, and the per-host service tokens the join returns are persisted host-locally (owner-only) so publishing and passkey fetches keep working after a join (#1770, #1772).
- Agents post to the coordination bus as themselves through an authenticated send proxy, so a message carries the agent's own identity and cannot be spoofed as another account (#1768).
- The cluster advertises the models a node can serve from its backend manifest, and installing a backend now registers it as a managed, node-local service that can be started, stopped, and health-checked per node (#1756, #1758, #1760, #1762).
- An approved external agent can be granted a least-privilege project-tasks scope to read and drive a single project's task board (claim, close, comment) with its own token, scoped so it can never reach another project (#1774).

### Fixed
- Backend and worker robustness: the model VRAM check reserves atomically before a load so two loads cannot race the same memory, a malformed backend manifest no longer crashes the worker, and the VRAM guard fails closed rather than open on a probe error (#1725, #1767).
- The RK3588 (RKLLM) install path pins the rkllama server to the verified 1.3.0 reference and guards the fork patches, and a live rkllama port is treated as installed only when it is a managed service (#1755, #1764).
- Fixed six agent-framework catalog manifests that referenced install scripts which did not exist at the repo root (#1694).

## [1.0.0-beta.38] - 2026-07-08

### Added
- The Agent conversation window now shows a live activity banner when a response is slow or has stalled. If no output arrives for a while it surfaces a "taking longer than usual" hint, escalating to a "may be stalled" warning with a shortcut to restart the AI services, so a stuck generation no longer looks like a frozen window. Requested by @mandresve (#1741).
- A "Restart AI Services" action in the Activity tab restarts the local inference backends (rkllama and qmd) without bouncing the controller or your agents, for recovering a stalled model on an edge device without a terminal. It asks for confirmation first and reports the result per service. Requested by @mandresve (#1743).

### Fixed
- The Agent conversation window now scrolls when a conversation is longer than the visible area, so long responses and logs stay reachable instead of pushing earlier content out of view. Reported by @mandresve (#1742).
- The Agents view is now readable on a phone. Archived agent rows stack so the agent name is no longer squeezed to a single character, and the header condenses so nothing truncates.
- Several other app views now reflow correctly on a phone instead of overflowing: the Images studio edit and library panels, the Tasks and Observatory lists, the add-agent dialog, and the Mail reading toolbar.

## [1.0.0-beta.37] - 2026-07-08

### Fixed
- An embedding model can no longer be assigned as an agent's chat model. Assigning one (for example qwen3-embedding-0.6b) now returns a clear error instead of silently accepting it and producing repeating, off-topic output, because an embedding model cannot do chat completion. Reported by @mandresve (#1740).
- A local RK3588 (RKLLM) model whose context window is too small for the agent harness now surfaces a non-blocking warning when it is assigned, so an over-small context (for example 4096 tokens) is flagged rather than silently truncating the agent prompt and looping (#1740).
- The RK3588 (RKLLM) backend now returns a structured context-overflow error when a prompt exceeds the model context, so a client can tell a context overflow apart from invalid input or a server fault instead of getting a bare 400. Reported by @mandresve (#1738).

### Added
- A `taos recover-password` command for offline recovery of a local account password when the admin is locked out of the web login. It resets the password directly in the auth store (single-user, named multi-user, pending, or legacy) and revokes that account's sessions.

## [1.0.0-beta.36] - 2026-07-08

### Fixed
- The RK3588 (RKLLM) install path no longer produces a broken rkllama service after an update. A previous pin bumped the rkllama server to a build that had dropped its startup preload flag, so the service failed to start on existing installs. The pin now points at a server that restores preload and adds a pre-flight context-length check, so a prompt longer than the model context returns a clear error instead of crashing the worker. Reported by @mandresve (#1730, #1732).
- SearXNG installs now enable the JSON output format by default, so an agent can use the local SearXNG as a search backend without hand-editing its settings (#969).
- Fixed a chat initialization error where a temporal-dead-zone reference could stop the conversation view from loading (#1720).

### Security
- Per-app secret files created during install are now written owner-only (0600) and regenerated if a prior write left them empty or malformed, so a session-signing key can no longer be left world-readable on disk (#1734).

### Changed
- taOS is now dual-licensed as AGPL-3.0-or-later plus a commercial option. The public core is AGPL-3.0, an OSI-approved license, with a separate commercial license available for uses that need different terms (#1721).

## [1.0.0-beta.35] - 2026-07-07

### Fixed
- The taOS Agent now returns output when it runs on a local RK3588 (RKLLM) model. Two gaps combined to make the agent report "the agent backend returned no output": the local rkllama backend was registered under a name that did not match the RKLLM model manifests, so the model was never exposed to the LiteLLM proxy the agent calls, and the pinned rkllama server had a Python version incompatibility that made its chat endpoint fail before inference. The backend name now matches on load (existing installs self-heal on update, no reinstall) and the rkllama pin is bumped to the fixed server. Reported by @mandresve (#1710).
- Agent message bubbles are now selectable and show a copy button on hover, so an agent's reply can be copied without dragging across the whole conversation (#835).
- The desktop top bar now shows a badge when an update is available, and clicking it opens the Updates pane in Settings (#855).
- taOS now surfaces a clear message when your local branch has diverged from its tracked remote, instead of a confusing update-check state (#841).

### Security
- Cluster GPU-lease endpoints now require admin authentication and validate their inputs, so a non-privileged LAN client cannot claim, release, or probe another node's GPU leases (#1675 follow-up).
- The bare-metal worker backend runs under process supervision with hardened handling, and container environment values now reject embedded newlines (#1691).
- Agent delegation and the org model were hardened against a stale-permission carry-over and a reporting-lock race (#174, #1661, #1662).

## [1.0.0-beta.34] - 2026-07-07

### Fixed
- Downloaded RK3588 (rkllama) models now appear after a normal update, with no reinstall. rkllama's background service kept saving models to its old location even after the controller updated, and taOS did not look there. taOS now reads where the rkllama service actually writes (from its systemd unit) and scans that directory, so an existing install's downloaded models show up in the Models list. Reported by @mandresve (#1548).
- The local RKLLM provider no longer shows Error because of a stale port. Installs seeded before the taOS default port moved to 7833 kept a localhost:8080 provider URL, so the Providers page and model discovery polled a dead port. That URL is now healed to 7833 on load. Reported by @mandresve (#1697).
- The taOS Agent chat no longer reports "runtime unavailable" when opencode was installed by the operator under their own home. The controller runs as an unprivileged service user that could not see an opencode installed in a different user's home, so it now also checks a TAOS_OPENCODE_BIN override and trusted system locations. Reported by @mandresve (#1616).
- When an agent's model change cannot re-scope its per-agent key, the stale key is discarded and that discard is now persisted, so the next deploy correctly falls back to the master key instead of reusing a key scoped to the old model (#1686).

### Security
- opencode discovery only probes trusted locations (system paths, the service user's own home) and an explicit operator override, never arbitrary users' home directories, so a non-privileged user cannot plant a binary the service would run (#1616).

## [1.0.0-beta.33] - 2026-07-06

### Fixed
- Downloaded RK3588 (rkllama) models now appear in the Models list. rkllama downloaded and registered models correctly, but wrote them to its own directory that taOS never scanned, so a model that finished downloading never showed up. taOS now points rkllama at the unified model directory it already scans, and migrates any models you have already downloaded into it on upgrade, so nothing needs re-downloading. Reported by @mandresve (#1548).
- rkllama install failures now surface the real cause (e.g. HuggingFace unreachable) instead of a generic "model not registered", so a failed download is self-diagnosing (#1548).
- Scheduled backups (and any other scheduled task) actually run now. The scheduler stored the cron expression but had no execution engine, so due tasks never fired (#165).

### Added
- Cluster GPU-lease coordination: agents claim and release a worker's GPU atomically over the A2A bus, and `/api/cluster/workers` now reports real-time free/used VRAM, so shared-hardware model loads on one node no longer collide. Archived models are promoted automatically when compatible hardware joins the cluster (#893, #333).

### Changed
- Backends (rkllama and other GPU/NPU model servers) run as the unprivileged `taos` service user with the device-group access they need, rather than root.
- Non-admin users no longer see system-settings panels, a UX follow-up to the settings-router access gate (#163).
- The documentation gate no longer trips on test-only files (#171).
- Cleared seven Dependabot alerts by pinning `lodash-es`, `uuid`, and `nanoid` (#173), and added a safe cleanup policy for stale agent worktrees (#172).

## [1.0.0-beta.32] - 2026-07-06

### Fixed
- Weather app location search works again. The app looks up cities and forecasts from the open-meteo API, but the Content-Security-Policy only allowed same-origin connections, so the browser silently blocked every lookup and the search field did nothing. The two open-meteo origins are now allowed. Reported by @mandresve (#1668).
- Desktop wallpaper no longer resets on login for anyone using a theme that declares a default wallpaper. Your explicitly chosen wallpaper is now authoritative on restore and is not overridden by the theme's default. Reported by @mandresve (#1603).

### Added
- Groundwork for the native iOS and watchOS client: a per-user device registry with revocable per-device scoped tokens, device management endpoints, a device-token auth path, and an APNs push sender (inactive until configured). No user-facing app yet; this is the server foundation the mobile app will build on (#1671).

### Changed
- Governance: answering a gated Decision no longer sends the asking agent a duplicate message, and delegation and retry replies now state honestly whether the action actually completed (#174).

## [1.0.0-beta.31] - 2026-07-06

### Fixed
- Model downloads on RK3588 (rkllama backend) really do show progress now. The earlier fix assumed the rkllama pull stream was JSON, but it streams plain-text percentage lines; taOS now parses those (and still handles the JSON form), so the bar advances instead of sitting at 0%. Reported by @mandresve (#1648).

### Added
- Agent org model: agents can carry a role and title and a reporting line (who reports to whom), viewable as an org tree, with cycle-safe validation. Agents can delegate a task to another agent through the existing governance gate, so a delegation is allowed, denied, or sent to the Decisions inbox for approval like any other gated action (#161).
- Agent heartbeat loop (opt-in, off by default): when enabled, taOS periodically wakes each idle running agent with its next ready task and that task's goal context, so agents pull and act on their queue on a schedule. Enable it with the `agent_heartbeat_enabled` setting (#164).

### Security
- Bumped cryptography to 48.0.1 to clear a high-severity OpenSSL advisory in the bundled wheels; the new version keeps wheels for every supported platform (including Intel Mac and 32-bit Windows), so no platform loses coverage (#1653).

## [1.0.0-beta.30] - 2026-07-05

### Fixed
- Model downloads on RK3588 (rkllama backend) no longer sit at 0% forever. Downloads that install through rkllama now report real progress as the weight is pulled, and a completed model is recorded and shown as installed immediately instead of looking stuck. Reported by @mandresve (#1648).

### Added
- Agent governance: per-agent LLM budget hard-stops. You can set a spend cap per agent; once an agent reaches its cap its model calls are rejected with a clear over-budget error before any request is dispatched. Spend accrues from real usage and the cap is settable and resettable via an admin API (#160).

### Security
- Updated frontend dependencies to clear known advisories (lodash-es code-injection and prototype-pollution, uuid, nanoid) via a grouped lockfile bump (#1655).

## [1.0.0-beta.29] - 2026-07-05

### Added
- Coding Studio has a real live preview: it renders your workspace's actual index.html in a sandboxed iframe, with local CSS, JS and images inlined and nothing fetched over the network, plus working desktop/tablet/phone size toggles. This replaces the old static mock.
- Music Studio can bounce a song to a downloadable WAV file, rendered offline via Tone.Offline so the export matches what you hear.
- Game Studio ships four new playable starter templates (endless runner, neon snake, sky tapper, asteroid miner), each a self-contained canvas game you can generate from, edit and share.

## [1.0.0-beta.28] - 2026-07-05

### Added
- App Studio is now real: describe an app in plain words and the taOS agent generates it, packages it, runs it through the security analyzer, installs it, and shows it running live in a sandboxed window, all in one flow. Generated apps ship as sandboxed web apps with no elevated permissions.
- Licensing transparency: services whose model weights are non-commercial (MusicGen, MusicGPT, FLUX-Fill) now carry accurate weight-license metadata (the code license was already MIT, but the weights are CC-BY-NC), the Store shows a "Non-commercial weights" badge, and installing such a service now requires a one-time license acceptance. Nothing non-commercial installs silently.

### Fixed
- Video Studio generation is no longer a multi-minute blocking request that could time out or fail on a disconnect. Generation now runs as a background job: you get an immediate job id, the UI polls for progress, and a failed job always ends in a clear error state instead of hanging.

## [1.0.0-beta.27] - 2026-07-04

### Added
- Office Suite is now complete: a Database view joins Write, Calc and Presentations, so you can build simple tables (typed columns, rows, inline editing) that save alongside your other documents.
- Office AI: Write now has working Rewrite, Shorten, Continue and Change tone actions, and Calc has a working "Ask your data" panel, both powered by your taOS agent. AI edits in Write are a single undo step (one Ctrl+Z restores your original text), and untrusted document/spreadsheet content is passed to the model as clearly delimited data, not instructions.
- Web Studio can now generate a real website from a prompt via your taOS agent (with a safe fallback to templates), previews it in a sandboxed frame, and shares or installs it as a taOS app.
- Music Studio is now a playable browser DAW: a Tone.js audio engine with a multi-track timeline, piano-roll editor, drum step-sequencer and mixer, songs that save to your cluster, and MIDI/JSON export.
- Design Studio designs now save: open, rename and delete your canvases, which persist across sessions (the editor itself was already fully featured).

### Fixed
- Images Studio no longer misleads you when the Quality edit tier is unavailable: it now tells you when a request was served by the fast eraser (prompt ignored) and disables the Quality option when its model is not installed, instead of silently downgrading. Reported behavior aligned with what the backend actually does.
- Settings: changing a Dock setting no longer resets your wallpaper to the default on the next login. Partial settings saves now merge instead of overwriting the rest of your preferences. Reported by @mandresve (#1603, #1601).

## [1.0.0-beta.26] - 2026-07-04

### Added
- Projects now give a task its full relational context: an agent sees the goal ancestry behind a task (its project and parent-task chain) and what is blocking it, and that "why" is surfaced in the task view and injected into the assigned agent's context when the task becomes ready or is claimed (#158).
- Routines & Schedules: a project can now run recurring or triggered routines (cron schedule, inbound webhook, or manual/API trigger) that automatically create a task on the board and wake the assigned agent. Managed from a Routines tab in the Projects app; webhook triggers are per-token, rate-limited, and owner-only (#159).
- Agent governance (first slice): execution policies decide whether a deployed agent's tool call is allowed, denied, or needs human approval. By default the sensitive actions (host code execution and arbitrary outbound HTTP) require an approval that lands in the Decisions inbox; once approved, a short-lived grant lets the agent proceed. Policies are a global default with per-agent overrides; admin operators are never gated (#160).

## [1.0.0-beta.25] - 2026-07-04

### Security
- Fixed a high-severity missing-authorization vulnerability (GHSA-47g9-fwwp-hrfp, CWE-862) in the system settings router: every `/api/config` and `/api/settings/*` endpoint was served with no admin check, so an authenticated non-admin user could read and overwrite the full system configuration and trigger privileged actions (`git pull` + dependency reinstall + service restart, and switching the tracked update channel), causing configuration corruption or denial of service for the whole instance. The settings router now requires an admin session or the host local token; non-admin sessions are rejected with 403 before any handler runs. Reported by EQSTLab.

## [1.0.0-beta.24] - 2026-07-04

### Fixed
- taOS Agent: the agent chat no longer wrongly reports "runtime unavailable" when opencode is installed system-wide. taOS now finds opencode on the PATH and at its default install location, and when the runtime genuinely can't start it shows the real error instead of a misleading generic message. The taOS Agent dialog also no longer opens as a blank window when launched from the Launchpad or Dock. Reported by @mandresve (#1615, #1616).
- Settings: theme, wallpaper, dock position and dock icon size now persist across logout/login. They were applying in-session but a restore that ran before login completed left them reverting to defaults on the next session. Reported by @mandresve (#1601, #1603).
- Models & Providers: when a downloaded model can't be used because the backend that serves it isn't running, taOS now says exactly that and how to fix it (install/start the backend) instead of a generic "not found anywhere", and provider connection tests report the real failure reason instead of "unknown error". Reported by @mandresve (#1599, #1600, #1614).

## [1.0.0-beta.23] - 2026-07-04

### Security
- Fixed a high-severity missing-authorization vulnerability (GHSA-h24f-gp4c-8qjm, CWE-862): the skill-execution endpoint `POST /api/skill-exec/{skill_id}/call` ran built-in skills, including one that executes arbitrary Python on the host, without any authorization check, so an authenticated non-admin user could achieve remote code execution as the backend process user. Skill execution now requires an admin session or the host local token (the credential deployed agents authenticate with); a non-admin session is rejected with 403 before any skill code runs, with a defense-in-depth check at the code-execution sink. Reported by EQSTLab.

## [1.0.0-beta.22] - 2026-07-04

### Added
- Game Studio is now a real AI-assisted game maker. Describe a game and the taOS agent generates a complete, playable game from a starter template; edit it with a file editor, a live sandboxed preview and an AI chat that proposes changes; save games to your cluster; and share a finished game as a sandboxed taOS app (it installs through the same security-analyzed app runtime as any other) or export it as a package (#1602).
- One-tap local model backend install, per hardware. On a supported machine the setup checklist offers to install a local LLM backend for your device in a single tap, creating and starting the service and confirming it actually answers before marking it done. Rockchip installs rkllama; NVIDIA, AMD, Apple Silicon and CPU-only machines install a llama.cpp server that serves chat plus embeddings and reranking from one process, so the taOS agent and taOS memory share a single backend (#1597, #1608).
- Settings account: the taOS account is framed as the key to taOSgo, app sharing and a reserved taOS username for a future website and social presence, with a prompt to reserve your username; onboarding gains an optional step to sign in to your taOS account (#1593, #1595).

### Fixed
- Store: installing an NPU/local backend now genuinely installs and starts it. A backend that showed as installed but never actually ran is repaired: the install creates and enables the service, self-heals a half-installed machine, and only reports success once the backend answers (#1598).
- Models: models you download are now selectable in the taOS agent and accepted at deploy. RKLLM models register with rkllama on download instead of silently landing on disk where the agent could not find them, and the agent model picker lists locally downloaded models, not just cloud ones. Reported by @mandresve (#1599, #1600).
- Settings: theme and wallpaper choices now persist across sessions, and Desktop & Dock settings (dock size and position) actually take effect. Reported by @mandresve (#1601, #1603).
- Text Editor: typing works continuously again; the editor no longer loses focus after each keystroke. Reported by @mandresve (#1596).
- Files: deleting a file or folder now moves it to the Recycle Bin instead of permanently removing it, across your own workspace, agent workspaces and project files; restore or empty it from the Recycle Bin. Reported by @mandresve (#1604).
- Projects: the New Project dialog no longer opens behind the Projects window. Reported by @mandresve (#1605).
- App Runtime: the install-time permission consent dialog can no longer be dismissed mid-request, and skips a redundant lookup when no consent is needed (#1592).

## [1.0.0-beta.21] - 2026-07-03

### Added
- App Runtime: install-time permission consent. Installing a sandboxed app now shows a dialog listing the capabilities it requests, with sensitive ones (network, agent, model, memory) highlighted, and you grant or deny before the app can use them (#1579).
- App Runtime: container app tier. Apps can now ship as their own container alongside sandboxed web apps, deployed on a per-app port bound to localhost with memory and CPU caps and reached through an isolated proxy (#1580).

### Fixed
- Store: installing an agent framework (Hermes, OpenClaw) now actually does something. It enables the framework for deployment in the Agents app, downloads its base image in the background with real progress, and notifies you when it is ready; the framework's Open action now takes you to the Agents app to deploy. Previously the install silently did nothing while showing "installed". Reported by @mandresve (#1582).
- Models: deleting a model now removes it on the backend instead of only hiding it in the UI, and a freshly downloaded model shows as installed immediately without reopening the dialog. Reported by @mandresve (#1581, #1548).
- Providers: providers no longer show a false Error (or a contradictory Running and Error together) on a healthy install. The panel reports true backend status, the rkllama default port matches the installer, and the toggle switches render correctly. Reported by @mandresve (#1578).
- Settings: the Logs pane now shows real system logs (controller, model backends, LLM proxy) with a live tail, not just browser-side errors, so backend failures are actually visible. Reported by @mandresve (#1583).
- Text Editor: creating notes and documents works again over plain http. The editor no longer crashes on a missing secure-context API, and the same class of bug was hardened across the assistant panel, push registration, and Web Studio. Reported by @mandresve (#1584).

## [1.0.0-beta.20] - 2026-07-03

### Added
- Video Studio: a new AI video-generation studio. Describe a scene, pick a resolution and duration, and generate a clip on any discovered video backend (WanGP / Wan 2.1). Generated clips land in a library with inline playback, download and delete (#1572).
- App Studio: a static security analyzer now scans AI-authored app source before install. It runs server-side on every install regardless of how the package was submitted or its type, flags risky patterns (unvalidated postMessage origins, storage exfiltration, code that executes strings), and blocks an install outright on a critical finding. App Studio's Publish view surfaces findings while the app is still just generated text (#1573).
- Security: app capabilities are now keyed to provenance. First-party apps keep the full capability set; AI-generated and user-uploaded apps are ceilinged to notifications and their own window; unknown-provenance apps get nothing until the user grants more (#1574).

### Fixed
- The macOS .app build works against current dependencies again, and its SPA static layout is resolved correctly whether or not the frontend build nests its output (#1557).

## [1.0.0-beta.19] - 2026-07-03

### Added
- Design Studio is now a real canvas editor. Select, move, resize and rotate elements; add editable text, shapes and images; manage layers; zoom and pan; undo and redo; and export the artboard to PNG. AI-generated images from the Magic view drop straight onto the canvas as editable elements (#1566).
- Web Studio: a new AI-assisted, Wix-style website builder. Describe a site or start from a template, then edit it as stacked sections (hero, features, gallery, contact and more) with inline text, image swaps, live theming, and add/remove/reorder. Preview responsively across desktop, tablet and mobile, and export a self-contained static HTML page. Sites persist on your own cluster (#1567).

### Changed
- Office Suite is now Office Studio, and it is a real office suite. Write is a full rich-text word processor (bold/italic/underline, headings, lists, links); Calc is a real spreadsheet with a formula engine (cell references, SUM/AVERAGE/MIN/MAX/COUNT/IF), multiple sheets, sort/filter and CSV import/export; and Slides is a real presentation editor with layouts, images, a fullscreen present mode and PDF export. All three save to your cluster (#1565, #1568, #1569).

### Fixed
- Models: downloading a model actually downloads it now. The Models app was showing a fake progress bar and a false "installed" state without ever contacting the backend, so the model vanished on reopen and no file was fetched. Download now calls the real backend, shows true progress, reflects the backend's installed state, and surfaces a real error with a Retry button on failure. Reported by @mandresve (#1548).

## [1.0.0-beta.18] - 2026-07-03

### Fixed
- Installer: agent-container runtime install now prefers the `incus-base` package over the full `incus` metapackage, whose extras have unsatisfiable dependencies on Debian Bookworm ARM64 (held broken packages); falls back to `incus` where incus-base is not a candidate (#1555).
- Models: a download that finishes the transfer but wrote no data (or the wrong number of bytes) is no longer marked complete. Both the torrent and HTTP paths now validate the file exists, is non-empty, matches the expected size, and passes its checksum before the model is reported installed (#1548).
- Installer: the RK3588 NPU install no longer aborts on the first clean run on boards that have binutils. The librknnrt version check piped `strings` (a 7MB binary) into an `awk` that exited on the first match; the early exit SIGPIPEd `strings`, which under `set -o pipefail` + `set -e` killed the whole install. It succeeded on a second run only because the pin was already applied and the block was skipped. The check now reads to EOF and is guarded (#1560, #1543). Reported by @mandresve.

## [1.0.0-beta.17] - 2026-07-02

### Fixed
- Models: a failed model download no longer looks like an instant successful install. The failure now stays on the model card with the actual cause from the backend (for example a checksum mismatch or an unreachable download host) and a Retry button, instead of the progress UI silently disappearing (#1548).
- Installer: the Docker Compose v2 plugin now installs on Debian (including vendor Pi images) by trying the Debian package name (`docker-compose-plugin`) before the Ubuntu one (`docker-compose-v2`), and the engine + plugin install in separate apt transactions so a missing plugin name can no longer prevent Docker itself from installing (#1541).
- Installer: install-rknpu.sh no longer aborts right after replacing librknnrt.so when `ldconfig` exits non-zero on vendor images with merged /lib layouts; the cache refresh is best-effort and rkllama now installs in the same run (#1543).
- Installer: the prebuilt desktop bundle is used on re-runs again. Re-runs as root over the taos-owned checkout tripped git's dubious-ownership check, which silently disabled the prebuilt path and forced a local vite build every time; the tree check now runs as the owning user, logs say whether the bundle channel was unreachable or genuinely mismatched, and installs pinned to a release tag fall back to the bundle attached to that release (#1544).
- Installer: install-rknpu.sh now installs the OpenCV runtime libraries rkllama needs (libGL, GLib, libSM, libXext); vendor Debian images ship without them and rkllama.service crashlooped on "ImportError: libGL.so.1" (#1545).
- Installer: when a vendor image ships with Docker preinstalled, incus is now installed as well (it is the preferred runtime for agent containers; skip with TAOS_NO_INCUS=1). Previously the installer treated the pre-existing Docker as sufficient and only a later warning told the user to install incus by hand and re-run (#1546).

## [1.0.0-beta.16] - 2026-07-02

### Added
- On Rockchip boards the setup checklist now includes an "Install the NPU backend" step: it appears only when an NPU is detected, opens the Store where the rkllama backend installs with one click, and ticks itself once the backend is running. Previously nothing in the setup flow ever surfaced the NPU install.

### Fixed
- A controller update or restart no longer silently strands agents in a paused state: agents whose framework handled the shutdown protocol itself, hostless agents, and agents whose containers boot slower than the controller are all resumed at boot (with background retries), and anything that still cannot be resumed raises a visible warning instead of staying paused quietly.

## [1.0.0-beta.15] - 2026-07-02

### Fixed
- The Rockchip NPU installer no longer fails on fresh installs with "reference is not a tree": the rkllama pin points at the proven production commit (made reachable for fresh clones again), and a stale pin fails with a clear explanation instead of a raw git error. This also keeps the generated rkllama service startable: the newer rkllama default branch dropped the preload option the service relies on. Reported by @mandresve (#1527, #1529).
- Reconnecting a comms channel (Telegram/Slack/Discord/email/Matrix/webchat) after a token rotation now stops the previous connector instead of silently leaking its background task, concurrent reconnects for the same agent can no longer orphan a connector, and a malformed Matrix reconnect fails cleanly without tearing down the working connector.
- The LLM proxy self-heal is more robust: it locates the install root at any venv layout, only trusts our own pyproject when doing so, and its pip fallback installs just the proxy requirements instead of re-resolving every dependency.
- Seeding the internal driver agents now requires naming each pre-existing handle explicitly to adopt it; a blanket adopt flag is rejected so a handle claimed by someone else can never be vouched for as a side effect.

## [1.0.0-beta.14] - 2026-07-01

### Added
- Channel Hub: Matrix connector. An agent can be reached over Matrix (homeserver + access token) the same way as Telegram/Slack/Discord; the connector mirrors the others and coexists with the A2A bus (channels are human-to-agent transport, A2A is agent-to-agent).
- Secrets: SSH keys are first-class on agent deploy. A secret in the `ssh-keys` category is materialized inside the agent container as `~/.ssh/<name>` with 0600 perms (path-traversal-guarded), so tools like git and ssh can use it directly instead of only as an env var.
- Store: the four optional social apps (Reddit, YouTube, GitHub, X) are installable again from the Store's taOS Apps section.
- Live notifications: a shared real-time event stream now pushes updates (starting with the notification bell) straight to the desktop, no more waiting on a poll or a page refresh.
- Observatory: per-session approval-mode control (default / accept-edits / don't-ask), the storage and API foundation for finer-grained control over how much an agent can do without asking.
- Action receipts: every tool call an agent makes is now recorded in an append-only, content-hashed audit trail (inputs and outputs fingerprinted), the foundation for verifiable replay.
- Agents panel now updates live as agents are minted, approved, or revoked, instead of needing a manual refresh.
- Auth: agents authenticate to the A2A bus with their own identity/token instead of borrowing the owner's session, and an admin can now adopt a pre-existing agent identity into the registry rather than being blocked.
- Contributor tooling: an enforced documentation-drift gate (local hook + CI) keeps README/docs in sync when scripts or install paths change.

### Changed
- Agent consent requests are now non-blocking: a bell entry and toast with inline Allow/Deny, instead of a desktop-blocking popup. Nothing is lost, decisions are archived to History.
- In-app updates now install exactly the dependency versions pinned in the lockfile (uv sync --frozen) instead of re-resolving on every update, so an update can no longer pull in an untested dependency version.
- The install directory moved from `/opt/tinyagentos` to `/opt/taos`; existing installs keep working with zero migration required.

### Fixed
- **Critical**: Settings > Install Update no longer uninstalls the LLM proxy (litellm); earlier updates could silently strip it and disable all agent LLM routing.
- The LLM proxy now self-heals: if litellm is missing at startup (e.g. left over from an update before the fix above), it reinstalls itself once automatically and comes back online.
- The desktop no longer breaks after an update: it reliably loads the new version after a redeploy instead of getting stuck on stale cached code or crashing when opening an app, on Chrome, Safari, and Firefox alike.
- A failed background rebuild during boot no longer crash-loops the controller; it now falls back to the last working build and logs a warning instead.
- Opening Settings > Account no longer flashes the login screen and bounces you back to System Info when you are not signed into a taOS cloud account; the account service's expected "not signed in" response is no longer mistaken for your device session expiring.

## [1.0.0-beta.13] - 2026-06-28

### Added
- Notes and Todo: tracked edits. An entry's text is editable and every change is an immutable revision tagged with editor and timestamp, stored as a diff with a full snapshot checkpoint every 20 edits so any past state can be reconstructed (Time Machine foundation). New history and at-revision endpoints expose the log and reconstructed text.
- Todo app: a checklist companion to Notes for `kind=list` documents, with per-task done checkboxes (completed tasks struck through), shared sharing/permission/agent-action controls, and the same tracked-edit history. Notes and Todo each show only their own document kind.
- Agent tool `notes_set_done`: an agent shared on a list (contributor or editor) can mark a task done or reopen it, completing the agent surface for shared todos. Membership, permission, archived-doc, and entry-belongs-to-doc are all enforced.
- Observatory fleet endpoint returns a health summary (total/working/idle/stale counts, stale handles, and an active/degraded/idle status) so the UI can show fleet status at a glance without recomputing.
- Notes "Discuss" agent action: an agent shared on a doc with the discuss action now gets a dedicated threaded topic channel (one per doc and agent, reused across entries, with the agent as a lead so it actively asks clarifying questions) instead of a DM ping, and falls back to the DM if the channel cannot be created.
- Observatory lane framework badges: the fleet endpoint now carries each agent's framework (kilo/opencode/hermes/...), and the Observatory app shows it as a small badge next to each lane handle.
- Coding sessions: the launch alias is editable. `PATCH /api/coding-sessions/{id}` renames a session, and the change is reflected in its agent-registry entry so the Agents/Registry app stays in sync.
- Coding sessions: host-folder launcher. `POST /api/coding-sessions/{id}/start` runs the chosen CLI in a detached tmux session scoped to the workdir, `/transcript` returns the captured terminal output (append-only), and `/stop` kills the tmux session (a no-op on an archived session).
- Frameworks: OpenCrabs registered as a beta agent framework (adolfousier/opencrabs, a single-binary Rust agent inspired by OpenClaw). A subprocess adapter drives `opencrabs run --format json` and maps the result onto the chat reply.

## [1.0.0-beta.12] - 2026-06-28

### Added
- Decisions app: the human-in-the-loop inbox. Store + API backend, desktop app, notification routing, answers routed back to the asking agent on the A2A bus, L1 supersede with history lineage, per-project Decisions archive tab, and a `request_decision` agent tool.
- Observatory app: fleet view of which agents are working on what, idle-agent surfacing, queue-control pause (global and per-lane), steer v1 (global and per-lane concurrency caps with server-rejection surfacing), and a stale-claim badge.
- Agent-native tools: `list_projects`, `list_tasks`, `list_files`, `list_frameworks`, `list_store_apps`, `get_capabilities` (hardware-aware advice), `notify_user`, and `request_decision`, plus a screen-aware desktop layout-read API for window management.
- Projects canvas: migration onto an MIT renderer (Konva foundation, Excalidraw read-only board with CanvasElement mapping), real mermaid/flowchart diagram rendering, GitHub issue to board-card sync, and channel project tagging with filter.
- Agent deploy: framework-aware prebuilt base image for a Hermes fast-path, Base Images management (API plus desktop pane: list/import/prune/prefetch), and an Import Agent wizard that uploads a Hermes profile bundle.
- Cluster: deploy an agent onto a cluster worker. An explicit target_worker pin creates the agent container on that worker's nested incus, with the controller reached over the LAN or tailnet.
- Notes and Todo: shareable notes and lists with an API (create/list docs, entries, members). A doc can be shared with agents, and a new entry notifies each agent member on its DM channel with the member's standing instruction so the agent can act.
- App permissions: a closed capability vocabulary with manifest validation, an `app_grants` ledger feeding the capability broker, and a request-consent endpoint that raises an app-grant Decision.
- Secrets broker: grant ledger and lifecycle (P0) plus routes and service wiring with request notifications (P1).
- Agent-model API: owner key-management (mint/list/revoke) and a `/v1/chat/completions` consent contract.
- Account pane shows the local signed-in identity and defaults the account base URL to taos.my.
- Logging: server-side and front-end crash capture with an in-OS Logs viewer.
- Messages groups agent DMs into Live / Suspended / Archived sections.
- Memory: arctic-embed-s as the recommended embedder default.
- taosctl gained command groups for decisions, observatory, agent-registry, dashboard, benchmarks, recycle, office, catalog, knowledge, templates, mail, store, settings, providers, secrets, themes, and notifications.
- Frameworks: added DeerFlow support.

### Changed
- Deploy Agent wizard shows Hermes as beta.
- Desktop app error boundary logs the real underlying error.

### Fixed
- Worker-LXC bring-up completed on Linux so workers are deploy-capable.
- Security: the skill workspace resolver rejects a traversal `agent_name`.
- Hermes profile import passes `--name` so the default profile is no longer rejected.
- Client-log ring buffer prunes correctly across rowid gaps and uses a rowid tie-breaker.
- Agent archive fails soft on snapshot-restricted projects and resolves or cleans orphaned containers on delete; an agent DM channel is archived on every removal path rather than orphaned.
- Auth: a correct password is no longer refused during lockout.
- Consent: request-consent skips capabilities with a pending Decision, de-dupes capabilities, and logs ledger failures; Decisions de-duplicate colliding option values and default an option value to its label.
- Observatory writes pause state atomically and no longer reverts an optimistic steer value mid-write.
- Update: dirty tracked source is stashed before pull instead of returning a 500.
- Dependencies: cryptography bumped to 48.0.1 for a vulnerable OpenSSL.

## [1.0.0-beta.11] - 2026-06-21

### Fixed
- Agent deploy: framework agents (Hermes, OpenClaw, etc.) failed to deploy on hosts that cannot mint per-agent LiteLLM virtual keys (ARM / Pi where prisma cannot start, and any install without Postgres). The deployer now falls back to the shared LiteLLM master key in genuine routing-only mode (single-user instances, with a loud warning; opt out via `TAOS_DISABLE_AGENT_MASTER_KEY_FALLBACK=1`). A DB-configured-but-broken mint still fails loudly so a real fault is never masked.
- Agent deploy: containers in a restricted multi-user incus project (e.g. `user-999`) failed at creation because proxy devices were forbidden. `add_proxy_device` now self-heals by allowing proxy devices on the named project and retrying once.
- Provider model picker: a newly-added cloud provider (e.g. DeepSeek) whose `/models` probe needs a key now surfaces its seeded models, and the taOS agent model chooser lists the same models as the agent deploy picker.
- Activity NPU card hidden on hardware with no NPU; desktop widgets default off until redesigned.

### Added
- Cluster: free-tier manual worker pairing. A worker prints its LAN address and a PIN; the user adds it from Cluster > Add worker with no network discovery (taOSgo remains the automated path).

### Changed
- Hermes is the recommended default agent framework (shown first and pre-selected in the deploy wizard); OpenClaw second.
- Dev/master version reconciled (beta.6 drift fixed) and bumped to `1.0.0-beta.11`.

## [1.0.0-beta.9] - 2026-06-21

### Fixed
- Install: a re-install over an existing virtualenv built with an unsupported Python (e.g. a 3.14 venv from an attempt before beta.8) reused that venv and failed with "requires a different Python: 3.14.x not in <3.14,>=3.11". The installer now detects an out-of-range venv interpreter and recreates the venv with a supported 3.11 to 3.13 Python.

## [1.0.0-beta.8] - 2026-06-21

### Fixed
- Install: the controller venv now uses a litellm-compatible Python (3.11 to 3.13). A fresh distro that defaults python3 to 3.14 (e.g. WSL on Ubuntu 26.04) previously aborted with "No matching distribution found for litellm>=1.89.3", because litellm supports only >=3.10,<3.14. The installer now picks a supported interpreter, installs python3.13 if none is present, and fails with a clear message otherwise; requires-python is capped at <3.14 to match.

## [1.0.0-beta.7] - 2026-06-21

### Fixed
- Install: libtorrent is no longer a core dependency, so a fresh install no longer aborts with "No matching distribution found for libtorrent>=2.0.9" on platforms without a libtorrent wheel (e.g. WSL). It is now an optional `torrent` extra; the model torrent mesh is enabled only where the OS-level package is present, and hosts without it fall back to a direct download.

## [1.0.0-beta.6] - 2026-06-21

### Added
- Coding Studio gains a model-agnostic tool-calling loop: agents read, edit, and verify files inside a workspace-jailed sandbox using filesystem tool primitives, driven by a LiteLLM-backed model step.
- Cluster capability map: worker registration and heartbeats populate a per-node capability and hardware map with admin endpoints, plus a non-destructive stale-node offline sweep.
- Append-only board audit log: every task transition is recorded, with a project-scoped activity feed and a task audit endpoint, indexed for unbounded growth.
- `taos rollback`: a CLI recovery path that restores the previous branch and version, so a broken update can be recovered even when the dashboard is unreachable.

### Changed
- One Browser app: the separate streamed-browser app is gone. The Browser app attaches a Neko streamed session through a toggle, and a RAM-capable Pi host can serve the session itself instead of reporting that it is not capable.
- The default store no longer seeds the X, Reddit, YouTube, and GitHub apps; they are optional installs.

### Fixed
- Browser sessions resolve the target worker before creating the session row, so a failed placement no longer leaves an orphaned session.
- Auto-expiring notification toasts no longer archive themselves into the History view.
- Dependabot majors updated: actions/checkout v7, dependabot/fetch-metadata v3, and the dev Python dependency group.

## [1.0.0-beta.5] - 2026-06-20

### Added
- Browser app redesigned to the current design bar with a collapsible sidebar.
- Coding Studio: workspace-scoped agent file edits with a build loop and inline diff review.

### Changed
- CI runs the test matrix on GitHub-hosted runners, cancels superseded runs per ref, and auto-merges low-risk Dependabot patch and minor updates on green.

### Fixed
- Streamed browser now connects over Tailscale and other non-LAN addresses: WebRTC advertises the single connecting-host IP, fixing the white screen the previous comma-separated NAT mapping caused.
- The "connecting" overlay can no longer hang over a session that is already live.
- Hardened the streamed-browser iframe sandbox and several store and coding-studio endpoints: IDOR guard on submission reads, symlink-safe workspace writes, and an admin gate on install-registry mutations.
- Store submissions return 400 on invalid input instead of 500.
- Security: dompurify updated to 3.4.11; cryptography and pydantic-settings advisories cleared.
- Install: the core install no longer aborts when optional components fail, and drops to the service user without assuming sudo (WSL robustness).

## [1.0.0-beta.4.1] - 2026-06-20

### Changed
- Installs and in-app updates verify the prebuilt bundle's SHA256 before extracting; a corrupted or tampered bundle is rejected and falls back to a local build.
- Re-installs update the existing install in place instead of forking a second copy.

### Fixed
- Symlink-safe staging (no fixed /tmp paths as root), atomic-rename swap, and a fix so the bundle is no longer treated as perpetually stale.
- README corrected (installs download a prebuilt bundle, no local build) and links rebranded to jaylfc/taOS.

## [1.0.0-beta.4] - 2026-06-20

### Added
- "Reduce effects" toggle (Settings, Accessibility) for low-end devices: disables background blur, heavy shadows, and continuous animations for a smoother UI on older hardware.

### Changed
- The installer and in-app update download a prebuilt UI bundle instead of building it locally, so installs and upgrades are faster and no longer fail or silently stay on the old version on low-memory machines including WSL. A local build, when still needed, now fails with a clear message instead of half-updating.
- CI runs on self-hosted runners and gates the desktop test suite.

## [1.0.0-beta.3] - 2026-06-16

### Added
- Mobile Store redesigned into an Apple App Store-style layout: bottom tab bar (Discover/Apps/Agents/Search/Updates), a featured hero, horizontal app carousels with Get pills and star counts, full-screen search, and a device filter.
- Real cover banners and icons across the Store: OpenClaw, Hermes, Ollama, ComfyUI, n8n, and the self-hosted apps, plus a shared Stable Diffusion banner (the AUTOMATIC1111 build shown in grayscale to distinguish it). A shared AppIcon component falls back to a branded monogram when no logo exists, so no tile renders blank.

### Fixed
- Installed apps in the mobile Store no longer show a non-interactive "Open" control; they show an honest installed status.
- Failed Store installs now surface a Retry action instead of failing silently.
- Store icons and cover images reset correctly when a reused tile switches to a different app.

## [1.0.0-beta.2] - 2026-06-16

### Added
- Mail app with IMAP/SMTP account setup, message list, read, and send.
- Reddit, YouTube, GitHub, and X apps available as optional Store installs.
- Agent-callable screenshot endpoint for desktop-control workflows.

### Changed
- Browser app redesigned with the Store/Images design bar and taos.my set as the default homepage, with automatic dark/light scheme applied to proxied sites.
- Projects app shell redesigned with a Workspace hero tab.
- Notification bell wired to the backend feed with actionable click routing to the originating app or agent.
- Updates panel now shows version numbers (e.g. 1.0.0-beta.2) as the primary display, with commit SHAs as a secondary detail.

### Fixed
- Controller restart time reduced from ~46 s to ~7 s by eliminating the graceful-stop hang.
- Projects canvas crash caused by malformed element payloads written by agents.
- Window move and resize jitter under rapid pointer events.

## [1.0.0-beta.1] - 2026-06-09

Initial source-available public beta release under the taOS Sustainable Use License v0.1.
