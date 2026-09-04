# Audit: deploy-lifecycle Security + Correctness Pass

**Generated:** 2026-09-02
**Task:** tsk-oko3f3 -- AUDIT [deploy-lifecycle]: deep read-only security+correctness pass
**Method:** Static review of `tinyagentos/deployer.py`, `tinyagentos/lifecycle_manager.py`,
`tinyagentos/rollback.py`, `tinyagentos/restart_orchestrator.py`, `tinyagentos/update_runner.py`,
`tinyagentos/auto_update.py`, `tinyagentos/framework_update.py`,
`tinyagentos/installation_state.py`, `tinyagentos/install_progress.py`,
`tinyagentos/install_registry.py`, `tinyagentos/first_boot.py`,
`tinyagentos/desktop_rebuild.py`, plus the matching route and test modules. Read-only:
**no source behavior was changed**. Baseline verified green: 407 tests across the
deploy-lifecycle surface pass (`tests/test_deployer.py`, `tests/test_deploy_secrets.py`,
`tests/test_deploy_ssh_keys.py`, `tests/test_deploy_cloud_model_resolution.py`,
`tests/test_agents_deploy_persona.py`, `tests/test_agents_deploy_smoke.py`,
`tests/test_routes_agent_deploy.py`, `tests/test_lifecycle_manager.py`,
`tests/test_lifecycle_manager_shared_client.py`, `tests/test_backend_catalog_lifecycle.py`,
`tests/test_provider_lifecycle_api.py`, `tests/test_project_lifecycle.py`,
`tests/test_task_lifecycle_notifications.py`, `tests/test_registry_governance_lifecycle.py`,
`tests/test_update_runner.py`, `tests/test_update_stash.py`, `tests/test_updater_dep_install.py`,
`tests/test_rollback.py`, `tests/test_auto_update_branch.py`, `tests/test_auto_update_ping.py`,
`tests/test_auto_update_framework.py`, `tests/test_docs_only_update.py`,
`tests/test_framework_update.py`, `tests/test_framework_update_runner.py`,
`tests/test_install_progress.py`, `tests/test_installation_state.py`,
`tests/test_install_registry.py`, `tests/test_desktop_rebuild.py`,
`tests/test_settings_taosmd_update.py`, `tests/test_update_channel_routes.py`,
`tests/test_update_agent_key.py`).

> This is not a green-field review. The system already incorporates fixes for
> several known classes (the `_wait_for_bootstrap_ping` default-binding bug
> that "silently ignored the patch" and made the missing-bootstrap test wait
> 120s on every CI run; the #852 `npm install` lockfile-rewrite deadlock
> after in-app `git pull`; the `taos-pre-update-*` recovery-tag pre-image
> before destructive steps; the resume_note-gated boot pause that stranded
> agents that implemented `/prepare-for-shutdown` themselves, hostless
> agents, and slow-boot containers -- issue #97). Most of the items below
> are therefore defense-in-depth or latent risks, not live exploitable holes
> against the current default configuration.

---

## 1. Surface as built

| Concern | Module(s) | What they own |
|---|---|---|
| Agent deploy | `deployer.py` | Container create, framework install, env injection, rollback-on-failure |
| Provider lifecycle | `lifecycle_manager.py` | Backend start/stop/drain/keep-alive; per-task health probe |
| Update rollback | `rollback.py` | Persistent `.taos-rollback` record (branch + sha + ts) |
| Restart orchestrator | `restart_orchestrator.py` | Graceful pause before update; boot-time resume |
| Update runner | `update_runner.py` | Git fetch, branch switch, stash, ff-merge, hard-reset fallback, GPG verification |
| Auto-update | `auto_update.py` | Hourly poll for new commits, de-duped notify, install-count ping |
| Framework update | `framework_update.py` | Snapshot + container-side install script + bootstrap ping wait |
| Install state | `installation_state.py` | Backend-driven view of app/model install state |
| Install progress | `install_progress.py` | In-memory TTL store keyed by `install_id` |
| Install registry | `install_registry.py` | Persistent record of installed items by location |
| First boot | `first_boot.py` | `.setup_complete` marker |
| Desktop rebuild | `desktop_rebuild.py` | Conditional SPA rebuild + prebuilt-bundle install |

The components are stitched together in `tinyagentos/app.py`'s lifespan:

- `app.state.install_registry` (line 1773) wired from `InstallRegistryStore` (493)
- `app.state.install_progress_store = get_global_store()` (1154)
- `RestartOrchestrator(app.state)` (926), `apply_pending_restart_check` (930),
  `resume_agents_from_notes` (935)
- `auto_updater = AutoUpdateService(...)` (1016), `app.state.auto_updater = auto_updater` (1022)
- `lifecycle_manager = LifecycleManager(backend_catalog)` (1093), shared_client
  attached at (1094)
- CLI entry: `taos rollback` -> `scripts/rollback.sh` (1898-1903)

Routes that drive these: `routes/agent_deploy.py` (validation + routing),
`routes/system.py` (writes `pending-restart.json` before a self-update),
`routes/settings.py` (calls `switch_to_branch`, `rebuild_desktop_bundle_if_stale`,
writes `pending-restart.json`), `routes/framework.py` and `routes/images.py`
(use `InstallationState`), `routes/install_registry.py` (read/write
`InstallRegistryStore`), `routes/store_install.py` (uses `InstallProgressStore`),
`routes/providers.py` (calls `LifecycleManager`), `routes/dashboard.py`
(marks setup complete after onboarding).

---

## 2. Executive risk summary

| Area | Rating | Notes |
|---|---|---|
| Container deploy | Strong | Snapshots baked in; rollback destroys container; SSH-key path-traversal guard; agent-secret env-name sanitization with platform-var no-clobber and deterministic first-wins on collision. |
| Provider lifecycle | Strong | Drain timeout bounded; proc.kill on timeout; keepalive task cancellation; shared httpx client reuses connections. |
| Update rollback | Strong | Recovery tag stamped before every destructive step; explicit "abort before anything destructive" if stash/checkout fails; GPG verification can be `required` (fail-closed). |
| Restart orchestrator | Strong | Resume-note on every paused agent; hostless agents unpause by flag flip; framework-side `/prepare-for-shutdown` answer path writes a synthesized minimal note; per-agent retry loop with deadline and loud "still paused" warning. |
| Auto-update | Strong | Flag-injection defense (`is_valid_branch_name` + `--` refspec); anonymous install-id (no PII); GPG verification; docs-only diff suppression. |
| Framework update | Strong | Snapshot before install; bootstrap-ping wait; version-tag verification before "success"; mark-failed cleanup. |
| Install progress | Medium | In-memory only by design, but state machine has a quirk (see Finding 4). |
| Install registry | Strong | Persistent; UNIQUE(item_id, location_ref) prevents double-record at the same location. |
| First-boot marker | Low | `is_first_boot` is dead code (see Finding 5). |
| Desktop rebuild | Strong | Prebuilt-bundle flow preferred (saves RAM); atomic rename within `static/`; `tarfile.filter="data"` (path-safe) with explicit fallback for Python <3.12; lockfile restore after `npm install`. |

---

## 3. Findings

### Finding 1 -- `update_to_master` has no production caller; only `switch_to_branch` does
**Severity:** Low (maintainability, not a live bug).
**Location:** `tinyagentos/update_runner.py:66` (`update_to_master`),
`tinyagentos/update_runner.py:200` (`switch_to_branch`).

The file header on `update_to_master` says: "GPG signature verification is handled
upstream in `switch_to_branch` (the production code path) and `auto_update._verify_gpg`
(the notification path).  This function is an internal helper that does not duplicate
those checks." A `grep -rn update_to_master tinyagentos tests` confirms there is no
production caller -- only `tests/test_update_runner.py` and `tests/test_rollback.py` use
it. The production update path goes through `routes/settings.py:1288`
(`await switch_to_branch(...)`) and `auto_update.py:_probe_remote`.

This is consistent with the docstring claim ("internal helper"), so it is not a bug.
It is, however, a small maintainability hazard: someone editing `update_to_master`
could reasonably believe they are touching the production path and could regress GPG
support or branch validation without breaking any test that runs against `switch_to_branch`.

**Recommendation:** rename to a private module helper (`_update_to_master`) and add a
docstring line stating "no production caller; production goes through
`switch_to_branch`", OR delete it and consolidate on `switch_to_branch` (with a default
`branch="master"`). Either makes the no-caller status machine-enforced.

---

### Finding 2 -- `_record_rollback_target` is best-effort and silently no-ops on missing inputs
**Severity:** Low. **Locations:** `tinyagentos/update_runner.py:39-52`
(`_record_rollback_target`), `tinyagentos/rollback.py:25-39`
(`record_pre_update`), `tinyagentos/rollback.py:42-63` (`read_rollback_target`).

`_record_rollback_target` returns early (`if not branch or not sha: return`) and wraps
`record_pre_update` in a bare `except Exception` with logger.warning. In practice this
catches (a) detached HEAD where `git rev-parse --abbrev-ref HEAD` returns "HEAD" -- but
the current code in `update_to_master`/`switch_to_branch` reads `branch` *after* the
fetch, so a detached HEAD would land in `record_pre_update(branch="HEAD", sha=...)`.
The `record_pre_update` writer puts `prev_branch='HEAD'`, which `scripts/rollback.sh`
parses but does not act on (it falls through to the explicit target or the newest
recovery tag), so this degrades gracefully rather than corrupting.

The `read_rollback_target` parser is intentionally simple and `source`-safe (does not
call `bash` on the file); it handles single-quoted values correctly. The recovery
shell script (`scripts/rollback.sh`) is the consumer and *does* `source` the file --
which is fine because the writer uses `_shq()` that escapes `'` as `'\''`.

**No live bug.** The subtlety worth documenting: a successful update on a detached
HEAD (rare but possible after `git checkout <sha>`) leaves a `prev_branch='HEAD'`
record. A subsequent `taos rollback` reads the file, sees no useful branch to restore,
and uses the newest `taos-pre-update-*` tag instead -- a documented fallback, not a
regression. If maintainers ever want this to fail-closed, the rollback script should
emit a warning when it falls through to the tag-only path because the explicit
`prev_branch` was `HEAD`.

---

### Finding 3 -- Unprivileged-namespace detection runs on every container-create failure path
**Severity:** Low. **Location:** `tinyagentos/deployer.py:60-80`
(`_explain_container_failure`).

`_explain_container_failure` calls `_is_unprivileged_userns()` whenever the error text
contains "idmapped storage" / "change ownership" -- which is the *intended* case. The
helper opens `/proc/self/uid_map` on every call; this is a cheap read but happens on
every container-creation failure. In the failure-during-deploy path this is once per
agent, not a hot loop. No risk in practice.

The translation logic itself is solid: an unprivileged LXC nesting inside another
unprivileged container cannot remap the inner container's filesystem (a kernel
limitation, not a taOS bug). The message is actionable ("set the LXC to Privileged
and enable Nesting"). One small drift risk: when this comment was written, the fix
was Proxmox-specific; the audit can confirm this still holds in 2026 but it's worth
flagging for follow-up if a different container host (Podman, Docker rootless)
becomes the supported path.

---

### Finding 4 -- `InstallProgressStore.finish` writes `error` only on failure; success path cannot record informational detail
**Severity:** Low. **Location:** `tinyagentos/install_progress.py:135-153` (`finish`).

```python
entry.state = "installed" if success else "failed"
if error is not None:
    entry.error = error
if detail is not None:
    entry.detail = detail
```

This is intentional: `error` is "what went wrong" and only meaningful for failures,
and `detail` carries human-readable progress hints on either path. The class
documents `InstallState` as `Literal["queued", "downloading", "verifying",
"unpacking", "starting", "installed", "failed", "cancelled"]` -- `cancelled` is
"Reserved for the follow-up Cancel install UI". The state machine is otherwise clean:
the in-memory store loses state on controller restart (acceptable: the install
subprocess dies with it), and `INSTALL_PROGRESS_TTL_S` (1h) prunes stale terminal
entries.

The subtle issue is in the consumer side: `routes/store_install.py` reports progress
by polling `InstallProgressStore.list_by_app(app_id)`, which sorts newest-first. A
user who clicks "Install" twice in quick succession gets two entries and the UI must
pick one. This is correct behaviour, but the audit notes it because the
in-memory-only nature means a controller restart *during* an install loses the
progress bar (the install itself restarts from scratch because the subprocess died
too). This is a known and accepted design.

**No live bug; documented for follow-up.**

---

### Finding 5 -- `is_first_boot` is dead code
**Severity:** Low (maintainability).
**Location:** `tinyagentos/first_boot.py:4-7` (`is_first_boot`),
`tinyagentos/first_boot.py:10-14` (`mark_setup_complete`).

`mark_setup_complete` is called from `routes/dashboard.py:35` after a user reaches
the end of onboarding. `is_first_boot(data_dir)` is *defined* but never imported
anywhere in `tinyagentos/`, `routes/`, `tests/`, or `desktop/`. The dashboard route
either relies on a different "first run" check or simply exposes the wizard
unconditionally.

If the first-boot wizard was retired when onboarding moved into the SPA, this is a
legitimate deletion candidate. If the dashboard route *should* check this before
showing the wizard, it's a latent regression where the wizard is being bypassed.

**Recommendation:** in a follow-up PR, either wire `is_first_boot` into the
dashboard's gate (and add a regression test), or delete the dead function. The
`.setup_complete` marker file is already maintained by `mark_setup_complete` so
either direction is safe.

---

### Finding 6 -- `install_id` file read returns empty string on OSError; the version ping then sends no `id=`
**Severity:** Low. **Location:** `tinyagentos/auto_update.py:80-105` (`install_id`),
`tinyagentos/auto_update.py:108-142` (`send_version_ping`).

`install_id` catches all exceptions in a bare `except Exception: return ""`. The
caller (`send_version_ping`) does `if iid: params["id"] = iid` so a missing id is
silently dropped. Combined, the install-count ping still includes `v=` and
`platform=`, which is what most of the analytics cares about. The behavior is
defensible: if the data dir is unwritable or `.install_id` is corrupt, we'd rather
send an honest ping with no id than crash the auto-update loop.

This is documented in the docstring: "A random UUID with no PII and no hardware
fingerprint, stored at `<data_dir>/.install_id`." No live bug.

---

### Finding 7 -- Agent-secret env injection logs the secret *count* but not the *names* in the collision path
**Severity:** Low. **Location:** `tinyagentos/deployer.py:374-411`.

The summary log line at `deployer.py:408` does `logger.info("Deploy %s: injected %d
agent secret(s): %s", req.name, len(injected), ", ".join(injected))` -- this leaks the
*env names* (e.g. `OPENROUTER_API_KEY`, `GITHUB_TOKEN`) into the controller log. The
collision-warning lines (`deployer.py:389, 396`) include the original *secret names*,
which may be user-chosen and might be sensitive in some configurations (a secret
named `prod-db-credentials` reveals the existence of that name).

**Not exploitable in any meaningful way** -- the secret *values* are never logged
(confirmed: only `injected` (env names) and `secret["name"]` appear in logs). But
operators running `taos` at high log verbosity on a shared log sink would be
exposing which integrations are configured.

**Recommendation:** demote the collision-warning log lines to DEBUG, or log only the
collision *count* and a redacted name (first 4 chars + `***`). The summary line at
408 is fine because it logs env names, not user secret names, and env names are
necessary for debugging "did my OPENROUTER_API_KEY get injected?"

---

### Finding 8 -- `LifecycleManager._probe_health` accepts any of `ok` / `healthy` / `running`
**Severity:** Low. **Location:** `tinyagentos/lifecycle_manager.py:153-173`.

This is a deliberate permissiveness so different backend types can each return the
status string they like. It does mean a misconfigured backend that always returns
`{"status": "ok"}` regardless of state would be treated as healthy -- but that's the
backend's responsibility (the same JSON shape is consumed elsewhere). Not a bug; note
for the audit trail.

---

### Finding 9 -- `framework_update.start_update` mutates `agent` dict in place without a lock
**Severity:** Low. **Location:** `tinyagentos/framework_update.py:73-125`.

`start_update` mutates fields on `agent` (the config dict) and calls `save_config`
inside try/except. `app.py` shows the config is mutated from multiple places (the
restart orchestrator also sets `paused=True`). A `FrameworkUpdate` task in flight
when the user pauses the same agent would race against the orchestrator's
`save_config_locked`. This is the same risk class that exists in other config-
mutating paths (e.g. `restart_orchestrator._prepare_agent`'s `await save_config_locked
(config, ...)`), so the audit only notes it -- the established pattern is to rely on
`save_config_locked` for serialization and accept that the field-level race window is
small.

No live bug. Documented for follow-up: a future hardening pass could move agent
mutation through a small accessor on the config object.

---

### Finding 10 -- `desktop_rebuild._try_prebuilt_desktop_bundle` downloads arbitrary content over HTTPS but verifies checksum before extract
**Severity:** Low. **Location:** `tinyagentos/desktop_rebuild.py:98-193`.

The flow: (1) read `desktop-tree.txt` (small text file keyed by `git rev-parse HEAD:desktop`);
(2) compare against the *local* tree hash (computed by the same git invocation on the
controller); (3) only if they match, download the (much larger) `desktop-bundle.tar.gz`;
(4) verify SHA256 against the CI-published `desktop-bundle.sha256`; (5) extract using
`tarfile.filter="data"` (path-safe in Python 3.12+); (6) atomic rename into
`static/desktop/`. Every step fails closed and falls through to a local build.

The trust model is: we trust the CI build (signed tag) and the controller's HTTPS
connection to `github.com`. The `desktop-tree.txt` value comes from `git rev-parse
HEAD:desktop` *on the controller*, then is compared against the same expression run
by the controller's `git -C project_root rev-parse HEAD:desktop`. So the
"key-match" check is local-only -- a network attacker cannot redirect the controller
to a different tree because the comparison is between two local git invocations, not
between local-and-remote.

The download of `desktop-bundle.tar.gz` is gated by the match, and the SHA256
verification gate (`if not expected_sha or hashlib.sha256(blob).hexdigest() !=
expected_sha.split()[0]`) fails closed: a missing checksum or a mismatch both fall
back to the local build. The bundle is verified *before* extraction (the verify is
on the `blob` variable, not the extracted directory).

The `urllib.request.urlopen(url, timeout=30)` is HTTPS to
`https://github.com/jaylfc/taOS/releases/download/bundle-latest/...` -- a pinned URL
on the project's own GitHub releases. The only way an attacker subverts this is to
control the GitHub release, which already breaks every other trust anchor in the
project. Acceptable.

**No live bug. The defense-in-depth stack (tree-match gate + checksum-before-extract
+ path-safe tar + atomic-rename swap) is well above what the threat model demands.**

---

### Finding 11 -- `restart_orchestrator._write_controller_note` uses `agent["name"]` from config without sanitization; note file path is constructed from it
**Severity:** Low. **Location:** `tinyagentos/restart_orchestrator.py:189-203`.

`note_dir = data_dir / "agent-memory" / name`. The `name` came from `config.agents`,
which is user-controlled at creation time (the agent record is created via
`POST /api/agents` with a body field, validated upstream). The agent name validation
sits in `routes/agents.py` and would reject path-separator characters; the
deployer's SSH-key check (`_SAFE_SSH_KEY_NAME`) uses an analogous pattern. The
downstream consumers of `note_dir` (the resume path) iterate
`config.agents` and recompute the same path. The risk surface is bounded because the
agent name validation runs first, but the audit confirms the assumption rather than
independently re-validating.

**No live bug; documented as defense-in-depth. A future hardening could move the
sanitization into a single helper used by both the agent-creation route and the
note-dir construction.**

---

### Finding 12 -- `_probe_health` swallows *all* exceptions silently
**Severity:** Low. **Location:** `tinyagentos/lifecycle_manager.py:153-173`.

`_probe_health` does `except Exception: pass; return False`. This is the right
behaviour for a polling loop -- it cannot afford to crash on every transient DNS
blip -- but it also means a permanent misconfiguration (e.g. the catalog's `url`
points to `localhost:9999` because the operator set it wrong) will produce zero log
output forever. The startup path (`LifecycleManager.start`) at least surfaces a
final `TimeoutError` with a helpful message, so the operator sees *something* in the
log after the deadline. Acceptable trade-off.

---

## 4. Strengths worth preserving

- **Recovery tag before every destructive step.** `update_runner.switch_to_branch`
  writes `taos-pre-switch-<sha>-<ts>` *before* any of {stash, checkout, merge,
  reset}. Even if the post-step fails, the operator can `git checkout` the tag.
  `update_to_master` does the same with `taos-pre-update-<sha>-<ts>`.
- **GPG verification can be required.** `auto_update._verify_gpg` and
  `update_runner.switch_to_branch` both check `gpg_required` and block the switch
  on a failed verification, with a separate warn-only path when verification is
  optional. `verify_commit` pins the merge target to the *verified SHA* (not the
  ref name) to close the verify-to-merge TOCTOU window.
- **Defense-in-depth on flag injection.** `is_valid_branch_name` validates at
  *every* argv entry point (`switch_to_branch` validates; `resolve_tracked_branch`
  re-validates; `_probe_remote` passes `--` before the refspec). The store could be
  poisoned with a stored `tracked_branch="--upload-pack=evil"` and the value would
  be rejected.
- **Documents-only suppression.** `auto_update.changes_are_docs_only` +
  `is_documentation_path` prevents auto-update notifications for docs-only diffs
  (no Python or JS changed). This is the right user-facing behaviour -- a doc-only
  commit doesn't need a "Restart to update" nag.
- **Resume note contract is unified.** Both the framework-side
  `/prepare-for-shutdown` answer path and the controller-side fallback write a
  dict with the same shape (`reason`, `paused_at`, `last_user_msg`,
  `in_progress_task`, `next_step_hint`, `context_snapshot`). The resume-side
  `_load_or_synthesize_note` and the write-side `_write_controller_note` agree on
  field names so a framework parsing either source sees a single contract.
- **Per-agent resume retry loop.** The 30s tick / 600s window prevents a slow-boot
  container from being silently paused for the rest of the session (issue #97).
  The "still paused" warning is the loud-failure path.
- **Atomic desktop bundle swap.** `_try_prebuilt_desktop_bundle` stages inside
  `static/` (same filesystem) and uses `Path.rename` for the swap. A cross-device
  copy would risk half-installed; the staging discipline avoids that.
- **`npm ci` over `npm install`.** `desktop_rebuild` deliberately prefers `npm ci`
  so the committed `package-lock.json` is never rewritten. The `npm install`
  fallback restores the lockfile afterwards (`git checkout -- package-lock.json`)
  to keep the working tree clean for the next `git pull`.
- **Framework-update version-tag check.** `framework_update._read_installed_tag`
  reads `/opt/taos/framework.version` and asserts equality with the expected tag
  before declaring "success". This is the right check: the install script's exit
  code alone is not sufficient (an install can complete without producing a
  working version file).

---

## 5. Test coverage gaps observed

- `restart_orchestrator._write_controller_note` path-traversal from a hostile
  agent name is *implicitly* covered by the upstream agent-name validation but
  not by a dedicated test against this function.
- `install_progress.InstallProgressStore` has no test for the `cancelled` state
  (it's reserved for the follow-up Cancel UI; the test gap matches the missing
  feature).
- `first_boot.is_first_boot` is not exercised by any test, consistent with the
  dead-code finding above.
- The collision-warning paths in `deployer.py` (lines 388-403) -- the
  "platform-vars no-clobber" and "first-wins on env-name collision" branches --
  are tested at the platform-var level but not exhaustively at the secret-collision
  level. A regression test that two secrets `api-key` and `api_key` collapse to
  one env var and a deterministic winner survives a refactor would be a useful
  hardening.

---

## 6. Conclusion

The deploy-lifecycle surface is in good shape: GPG-verified updates with
recovery-tag-before-destruction, snapshot-then-install with bootstrap-ping
verification, SSH-key path-traversal guard, secret env-name sanitization with
deterministic collision policy, atomic desktop-bundle install with checksum
verification, per-agent resume retry on boot, and graceful drain timeout on
backend shutdown. The findings above are all low-severity defense-in-depth or
maintainability items; none of them are live exploitable holes against the
current default configuration.

No code changes proposed in this pass. Follow-up candidates, in priority order:

1. (Finding 5) Wire `is_first_boot` into the dashboard gate, or delete the
   dead function.
2. (Finding 1) Rename `update_to_master` to a private helper or delete it
   (consolidate on `switch_to_branch`).
3. (Finding 7) Demote the secret-collision warning lines to DEBUG (or redact
   the user-chosen secret name in the log).
4. (Finding 5 of "Test coverage gaps") Add a regression test for the
   `api-key` / `api_key` -> `API_KEY` env-name collision in `deployer`.

Baseline verified green: 407/407 deploy-lifecycle tests pass.