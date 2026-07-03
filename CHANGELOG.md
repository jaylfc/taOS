# Changelog

All notable changes to taOS are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow semver beta: `1.0.0-beta.N`, bumped on each dev->master promotion.

## [Unreleased]

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
