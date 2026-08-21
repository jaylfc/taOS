# Changelog

All notable changes to taOS are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow semver beta: `1.0.0-beta.N`, bumped on each dev->master promotion.

## [Unreleased]

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
- The auth middleware's agent Bearer allowlist now covers `GET`/`POST /api/projects/{project_id}/tasks/{task_id}/checklist-items`, so a registry JWT reaches the handlers' `project_tasks_create` scope check instead of being refused 401 at the gate. Inert until the checklist routes (#2415) merge; DELETE and per-item subpaths stay session-only (#2430).
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
