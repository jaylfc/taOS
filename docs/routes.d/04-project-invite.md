# Project invite redeem route (link + PIN)

<!-- A project invite lets an external agent join without going through the consent UI. The mint dialog (admin, in the project's Members panel) creates the invite; the agent redeems it. Two endpoints are auth-EXEMPT (the PIN is the proof of possession) -->

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies the PIN (wrong / expired / attempt-capped → 403; already redeemed / revoked → 409)
- Derives the agent handle `{project_slug}-{harness}[-{label}]`
- De-duped against active registry agents in the project
- Auto-approves via `approve_request_record` (decided_by = the invite's creator) or leaves the request pending (manual mode)
- Returns a connection bundle plus `{request_id, agent_handle, poll_path}`
- `project_tasks` is force-included so a successful redeem always yields a project member

### GET /i/{invite_id}

Content-negotiated advert:

- `Accept: application/json` → gets the redeem contract (`{method, path, fields}`)
- Browser → gets a minimal HTML page
- No PIN check here; it only advertises the contract

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority ordered, operator override first) and the mesh (Tailscale) node IP when joined. No relay in Phase 1.
- `apis` — agent-JWT-reachable surface, scoped exactly to the granted scopes (mirrors the middleware canvas allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — personalized capability guide (repo link, agent manual links, scoped Projects/Canvas summary, the A2A authenticated-proxy contract)

See `docs/design/external-agent-project-invite.md` (issue #1780); canvas routes are advertised only when that scope was granted.