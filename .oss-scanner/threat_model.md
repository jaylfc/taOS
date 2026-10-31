# Threat model: taOS

## What this project does

taOS is a self-hosted AI agent operating system for home servers and small boards (Raspberry Pi, Orange Pi, mini PCs).
One Python process, the controller (`tinyagentos/`, FastAPI + uvicorn, default port 6969), serves:

- a web desktop SPA (`desktop/`, built into `static/desktop`) that the owner and other household users open in a browser
  on the LAN, and optionally over Tailscale;
- a JSON/WebSocket API under `/api/`, `/auth/`, `/ws/` used by the SPA, the `taosctl` CLI, paired devices and agents;
- agent lifecycle: agents run in LXC or Docker containers that the controller creates, and call back into the
  controller with their own scoped credentials;
- an app store and installer that deploy third-party apps and model backends from manifests (`app-catalog/`,
  `tinyagentos/catalog`, `tinyagentos/routes/store_install.py`, `tinyagentos/installers/`, `scripts/install-*.sh`).

The controller holds the household's secrets (LLM provider keys, agent secrets, user accounts) and can start
containers, so a controller compromise is a host compromise for practical purposes.

## Where untrusted input enters, and who the attacker is

Treat all of these as adversaries:

1. **Unauthenticated LAN client.** Anyone on the same network can reach port 6969. The controller is advertised by
   mDNS (`tinyagentos/services/mdns_publisher.py`). Every path in `EXEMPT_PATHS` / `EXEMPT_PREFIXES` and every
   method-sensitive exemption in `_is_exempt` (`tinyagentos/auth_middleware.py`) is reachable without a credential:
   lock-screen endpoints, `/auth/setup` (first-user onboarding), cluster pairing announce/claim/manual-claim and worker
   heartbeat, project invite redeem and `GET /i/<id>`, consent-loop status polls under `/api/agents/auth-requests/`,
   `/api/peer/`, `/shortcut/`, `/ws/`, and the LLM and agent-as-a-model surfaces (`/v1/...`, `/api/llm/v1/...`) where
   the route, not the middleware, checks the key.
2. **Cross-site browser requests.** A page on another origin that a logged-in user visits. Sessions are cookies
   (`taos_session`); CSRF protection is a token bound to the session (`tinyagentos/middleware/csrf.py`, minted per `taos_session` and re-issued on login) applied per router
   via `Depends(verify_csrf)` in `tinyagentos/routes/__init__.py`. A mutating route that is session-reachable but not
   covered by `verify_csrf` is a finding.
3. **A malicious or compromised agent holding a scoped token.** Agents authenticate with an Ed25519 registry JWT
   (`tinyagentos/agent_token_auth.py`, `tinyagentos/agent_registry_store.py`). The middleware passes a non-local Bearer
   only to an allowlist of paths (`_AGENT_TOKEN_PATHS` and the `_is_agent_*_path` matchers); the route must then check
   signature, active status, scope grant (`tinyagentos/agent_grants_store.py`), `token_min_iat` rotation and, for
   project routes, project binding. Assume the agent fully controls its container and every field it sends.
4. **A non-admin user on a multi-user host.** Household members have their own accounts. `request.state.is_admin` is
   set from the user record; per-user data (workspace files, memory, personas, shares) must not leak across users.
   User-to-user sharing is recorded in `tinyagentos/user_shares_store.py` and served by `tinyagentos/routes/user_shares.py`.
5. **A paired device holding a device token** (`Bearer taosdev_...`, `tinyagentos/device_store.py`), limited to the
   carded routes matched by `_is_device_bearer_path`.
6. **Malicious content.** App manifests and catalog entries, imported files (`tinyagentos/routes/import_data.py`,
   `tinyagentos/routes/agent_import.py` profile bundles), uploads (`tinyagentos/middleware/upload_body_limit.py` and
   the routes that register caps), chat and project files, and URLs the controller fetches on a user's or agent's
   behalf (knowledge ingest, canvas unfurl, browser proxy, push endpoints).

## Components that matter most

In rough priority order:

- `tinyagentos/auth_middleware.py`: the single gate. Credential precedence is local token, then registry JWT on
  allowlisted paths, then device bearer on carded paths, then session cookie. Anything that lets a weaker credential
  (or none) reach a route that assumes a stronger one is critical.
- `tinyagentos/auth.py`: password hashing (argon2), sessions bound to a SHA-256 of the creating User-Agent, the host
  local token (`.auth_local_token`, written 0600, maps to the primary admin), first-user setup.
- `tinyagentos/middleware/`: `csrf.py`, `security_headers.py` (CSP; the Host header is only interpolated when it
  matches a safe pattern), `upload_body_limit.py` (caps multipart bodies before Starlette spools them).
- `tinyagentos/routes/`: every handler that takes untrusted HTTP input. Of particular interest:
  `auth.py`, `agent_registry.py`, `agent_auth_requests.py` (external agent consent), `a2a_bus.py`, `cluster.py`
  (pairing), `user_shares.py`, `shared_folders.py`, `app_permissions.py`, `store_install.py`, `apps.py`,
  `userspace_apps.py`, `import_data.py`, `agent_import.py`, `project_files.py`, `chat_files.py`,
  `user_workspace.py`, `workspace.py`, `agent_workspace.py`, `peer.py`, `shortcut_proxy.py`,
  `desktop_browser/` (proxy, download, extract), `agent_model_api.py`, and `tinyagentos/llm_gateway/`.
- Path handling in file routes: anything that joins a user- or agent-supplied name onto a data directory. Path
  traversal, symlink following, or archive extraction that writes outside the intended root is in scope.
- SSRF guards in `tinyagentos/knowledge_ingest.py`, `tinyagentos/library_pipeline.py`,
  `tinyagentos/projects/canvas/unfurl.py`, `tinyagentos/push/unifiedpush.py`, `tinyagentos/peer.py`,
  `tinyagentos/installers/download_installer.py` and `tinyagentos/routes/desktop_browser/`.
- Grants and identity: `tinyagentos/agent_grants_store.py`, `tinyagentos/app_grants_store.py`,
  `tinyagentos/agent_tokens_store.py`, `tinyagentos/registry.py`, `tinyagentos/secrets.py`.
- Installer scripts: `install.sh`, `scripts/install-server.sh`, `scripts/install-worker.sh`, and the per-backend
  `scripts/install-*.sh`. Look for command injection from manifest or user-supplied values and for unverified
  downloads executed as root.

## Components that matter less, or are out of scope

- Third-party dependencies (FastAPI, Starlette, litellm, taosmd, etc.) and vulnerabilities that exist only in them.
  A taOS bug that misuses a dependency is in scope.
- Attacks that require local root, the taOS service account, or read access to the data directory. The local token
  file is deliberately the same-host trust anchor.
- Physical access to the device, and anything requiring a pre-compromised host (also excluded in `SECURITY.md`).
- Denial of service through unrealistic traffic volumes. Unbounded memory or disk use from a single small request is
  in scope (medium).
- `site/`, `docs/`, `mac/`, `os-build/`, `packaging/`, test code, and the third-party apps the store installs (as
  opposed to taOS's handling of their manifests).
- What an agent does inside its own container with its own credentials. Escaping to the host or to another agent's
  resources is in scope.

## How to exercise it

The image is backend-only (the SPA is not built). The venv is on PATH.

- Tests: `python -m pytest tests/ --ignore=tests/e2e -n auto`. Security-focused files include
  `tests/test_auth_middleware.py`, `tests/test_csrf.py`, `tests/test_agent_token_auth.py`,
  `tests/test_security_headers.py`, `tests/test_upload_body_limits.py`.
- Run a controller: `TAOS_DATA_DIR=/tmp/taos-data uvicorn --factory tinyagentos.app:create_app --host 127.0.0.1 --port 6969`,
  then poll `GET /api/health`.
- Onboard the first user (exempt from auth and CSRF): `POST /auth/setup` with JSON
  `{"username", "full_name", "email", "password", "auto_login": true}`. The response sets `taos_session` and
  `csrf_token` cookies; send the same User-Agent on later requests, and echo `csrf_token` in `X-CSRF-Token` on
  mutating requests. `.github/workflows/ci.yml` (job `desktop-e2e`) does exactly this.
- A second, non-admin user and an agent with a registry JWT can be created through the API to test the
  cross-user and agent-scope boundaries.

## How we rate severity

- **Critical:** unauthenticated (LAN) remote code execution on the host; unauthenticated admin account takeover;
  authentication bypass to any admin route; an agent token, device token or non-admin session reaching code
  execution on the host or another container; reading provider API keys or other users' secrets without a credential.
- **High:** a non-admin user or an agent escalating to admin; an agent acting outside its granted scopes or project
  binding with real impact (writing other projects, reading other agents' secrets); cross-user read or write of
  private files, memory or shares; CSRF on a state-changing route with security impact (account, credentials,
  grants, installs); path traversal or archive extraction that writes outside its root; SSRF to loopback or the LAN
  that reaches the controller's own admin surface or cloud metadata; stored XSS in the desktop that runs as an
  admin; command injection in an installer from a manifest or request field.
- **Medium:** information disclosure of non-secret but private data (usernames, agent lists, file names) to an
  unauthenticated client; CSRF on low-impact routes; SSRF limited to blind requests; reflected XSS; single-request
  resource exhaustion; brute-force protection bypass on PIN or pairing codes where the code space is still large.
- **Low:** missing hardening headers with no demonstrated exploit, verbose errors, timing differences without a
  practical attack.
- A finding needs a concrete path from an attacker position listed above. Theoretical issues with no reachable
  input are capped at low. Post-auth issues that only an admin can trigger against their own host are low unless
  they cross into another user or the host root.

## Things to leave alone

These are intentional and should not be reported on their own:

- The existence of the exempt paths listed in `auth_middleware.py`. Report them only if one leaks data or performs a
  write it should not, or if the matcher admits a path it should not.
- `POST /api/system/prepare-shutdown` accepting loopback callers without a credential (checked on the TCP peer, not
  on headers).
- `style-src 'unsafe-inline'` in the CSP (server-rendered auth pages).
- The local token granting admin: possession requires reading a 0600 file in the data directory.
- Agent-as-a-model and LLM gateway paths being middleware-exempt: the route dependency is the gate. Report if that
  dependency can be bypassed.
- Routes marked `csrf_bypass` in tests: these are tracked test-only debt, not a production setting.

## Report format

Please include: the attacker position (from the list above), the exact request or sequence of requests, the file
and line of the root cause, the impact, and a minimal patch with a regression test in `tests/` that fails before
the patch and passes after. Prefer one finding per report. Patches should keep the existing style: fail closed, and
keep the credential check in the route dependency when the middleware only passes a Bearer through.
