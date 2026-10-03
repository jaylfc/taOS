# Project invite redeem route (link + PIN)

<!-- A project invite lets an external agent join without going through the consent UI. The mint dialog (admin, in the project's Members panel) creates the invite; the agent redeems it. Two endpoints are auth-EXEMPT (the PIN is the proof of possession) -->

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies PIN (wrong/expired/attempt-capped → 403; redeemed/revoked → 409)
- Derives handle `{project_slug}-{harness}[-{label}]`, de-duped vs active registry agents
- Auto-approves via `approve_request_record` (decided_by = invite creator), or leaves pending (manual)
- Returns connection bundle + `{request_id, agent_handle, poll_path}`
- `project_tasks` force-included → successful redeem = project member

### GET /i/{invite_id}

Content-negotiated: `Accept: application/json` → redeem contract (`{method, path, fields}`); browser → minimal HTML. No PIN check; only advertises contract.

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority, override first) + mesh (Tailscale) IP when joined; optional relay when `TAOS_CONTROLLER_RELAY_URL` is `https://`
- `apis` — agent-JWT surface, scoped to granted scopes (mirrors middleware allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — capability guide (repo/manual links, scoped Projects/Canvas, A2A proxy, memory + timed checks). `harness=grok` adds secure-form token storage, onboarding polling, shared-account warning.

See `docs/design/external-agent-project-invite.md` (#1780); canvas routes only when scope granted.

## Override addresses advertised over HTTP

Operator sets `TAOS_CONTROLLER_CALLBACK_HOST`. Advertised over **HTTP**:

- Tailscale CGNAT `100.64.0.0/10` (e.g. `100.78.225.80`)
- Tailscale ULA `fd7a:115c:a1e0::/48`
- Private LAN (RFC 1918: `10/8`, `172.16/12`, `192.168/16`)
- Loopback (`127.0.0.1`, `::1`, `localhost`)
- Hostnames ending `.local`, `.lan`, `.home.arpa`, `.ts.net` (MagicDNS)
- Single-label (e.g. `taos`, `controller`)

**NOT advertised** (omitted with warning): public IPs, public hostnames (e.g. `controller.example.com`), hostnames not matching trusted suffixes.

Full URL override (e.g. `http://192.168.1.5:6969`) uses scheme/port as-is; LAN IP enum deduplicates vs parsed hostname.

**Relay** (`TAOS_CONTROLLER_RELAY_URL`): `https://` only. HTTP relay URLs omitted with warning.