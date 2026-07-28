# Changelog

All notable changes to taOS are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow semver beta: `1.0.0-beta.N`, bumped on each dev->master promotion.

## [Unreleased]

### Fixed

- **Push notifications never routed to the correct app.** Three independent faults
  in the service-worker click path meant tapping any notification either opened
  the desktop root or did nothing: the backend dropped routing fields before the
  SW saw them, the deep-link fallback was always root, and no shell listener
  existed for the common mobile-PWA case where the app is already open. Decision
  pushes now open Decisions (or the mapped target app) whether or not a window
  is already open, and the cross-origin guard remains intact.

## [1.0.0-beta.44] - 2026-07-26

### Added

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
