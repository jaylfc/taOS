# Project invite redeem route (link + PIN)

<!-- A project invite lets an external agent join without going through the consent UI. The mint dialog (admin, in the project's Members panel) creates the invite; the agent redeems it. Two endpoints are auth-EXEMPT (the PIN is the proof of possession) -->

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies the PIN (wrong / expired / attempt-capped → 403; already redeemed / revoked → 409)
- Derives the agent handle `{project_slug}-{harness}[-{label}]`, de-duped against active registry agents in the project
- Auto-approves via `approve_request_record` (decided_by = the invite's creator), or leaves the request pending (manual mode)
- Returns a connection bundle plus `{request_id, agent_handle, poll_path}`
- `project_tasks` is force-included so a successful redeem always yields a project member

### GET /i/{invite_id}

Content-negotiated advert: `Accept: application/json` → the redeem contract (`{method, path, fields}`); browser → a minimal HTML page. No PIN check here; it only advertises the contract.

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority ordered, operator override first) and the mesh (Tailscale) node IP when joined; optional relay endpoint when `TAOS_CONTROLLER_RELAY_URL` is configured with `https://`
- `apis` — agent-JWT-reachable surface, scoped exactly to the granted scopes (mirrors the middleware allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — capability guide (repo + manual links, scoped Projects/Canvas summary, A2A proxy contract, memory + timed checks). `harness=grok` adds secure-form token storage, onboarding polling, and a shared-account warning.

See `docs/design/external-agent-project-invite.md` (issue #1780); canvas routes advertise only when that scope was granted.

## Override addresses advertised over HTTP

The operator can set `TAOS_CONTROLLER_CALLBACK_HOST` to control which address the invite bundle advertises for the controller. The following override values are advertised over **HTTP** in the bundle:

- **Tailscale CGNAT IPs** in `100.64.0.0/10` (e.g. `100.78.225.80`)
- **Tailscale ULA IPs** in `fd7a:115c:a1e0::/48`
- **Private LAN IPs** (RFC 1918: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- **Loopback** (`127.0.0.1`, `::1`, `localhost`)
- **Hostnames** that end with `.local`, `.lan`, `.home.arpa`, or `.ts.net` (MagicDNS)
- **Single-label hostnames** with no dot (e.g. `taos`, `controller`)

The following override values are **NOT advertised over HTTP** (omitted with a warning):

- Public IP addresses
- Public hostnames (e.g. `controller.example.com`, `api.mycompany.com`)
- Any hostname not matching the trusted suffixes above

When the override is a full URL (e.g. `http://192.168.1.5:6969`), the scheme and port are used as-is, and the LAN IP enumeration deduplicates against the parsed hostname to avoid advertising the same address twice.

**Relay endpoint** (`TAOS_CONTROLLER_RELAY_URL`): Must be `https://` only. HTTP relay URLs are omitted with a warning, even for private addresses.
