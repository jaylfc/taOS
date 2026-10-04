# Project invite redeem route (link + PIN)

<!-- A project invite lets an external agent join without going through the consent UI. The mint dialog (admin, in the project's Members panel) creates the invite; the agent redeems it. Two endpoints are auth-EXEMPT (the PIN is the proof of possession) -->

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies PIN (wrong/expired/attempt-capped → 403; already redeemed / revoked → 409)
- Derives agent handle `{project_slug}-{harness}[-{label}]`, de-duped against active registry agents
- Auto-approves via `approve_request_record` (decided_by = invite's creator); or leaves the request pending (manual)
- Returns connection bundle + `{request_id, agent_handle, poll_path}`
- `project_tasks` is force-included so a successful redeem yields a project member

### GET /i/{invite_id}

Content-negotiated: `Accept: application/json` → redeem contract (`{method, path, fields}`); browser → minimal HTML. No PIN check; only advertises contract.

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority, operator override first), the mesh node IP when joined; relay endpoint only when `TAOS_CONTROLLER_RELAY_URL` is `https://`
- `apis` — agent-JWT-reachable surface, scoped to granted scopes (mirrors the middleware allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — capability guide (repo + manual links, scoped Projects/Canvas, A2A proxy, memory + timed checks). `harness=grok` adds secure-form token storage, onboarding polling, and a shared-account warning.

See `docs/design/external-agent-project-invite.md` (issue #1780); canvas routes only when scope granted.

## Override addresses advertised over HTTP

The operator can set `TAOS_CONTROLLER_CALLBACK_HOST` to control which address the invite bundle advertises for the controller. Advertised over **HTTP**:

- **Tailscale CGNAT IPs** in `100.64.0.0/10` (e.g. `100.78.225.80`)
- **Tailscale ULA IPs** in `fd7a:115c:a1e0::/48`
- **Private LAN IPs** (RFC 1918: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- **Loopback** (`127.0.0.1`, `::1`, `localhost`)
- **Hostnames** that end with `.local`, `.lan`, `.home.arpa`, or `.ts.net` (MagicDNS)
- **Single-label hostnames** with no dot (e.g. `taos`, `controller`)

**NOT advertised** (omitted with a warning):

- Public IP addresses
- Public hostnames (e.g. `controller.example.com`, `api.mycompany.com`)
- Any hostname not matching the trusted suffixes above

Full URL overrides (e.g. `http://192.168.1.5:6969`) use scheme/port as-is; the LAN IP enumeration deduplicates against parsed hostname.

**Relay** (`TAOS_CONTROLLER_RELAY_URL`): Must be `https://` only. HTTP relay URLs are omitted with a warning.