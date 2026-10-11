## Screencast cast tokens (`/cast/media/`, token-only)

A TV (Chromecast, Apple TV) fetches cast media ITSELF and carries no taOS
session, cookie or bearer. So casting never hands a TV an ordinary taOS URL:

- `POST /api/cast/sessions` (session-only) `{view: "canvas:<project_id>",
  device_id, mode: "still", device_address?}` mints a cast for ONE view the
  caller owns (non-owner: existence-hiding 404) and returns the token once,
  plus `media_url`, `expires_at`, `ttl_seconds` and `refresh_interval_seconds`
  (how stale a still frame can be; show it in the UI). `mode: "live"` answers
  501 until the HLS encoder lands.
- `POST /api/cast/sessions/{cast_id}/refresh` extends an ACTIVE cast by one
  TTL; an expired or ended cast answers 409 and cannot be revived.
- `DELETE /api/cast/sessions/{cast_id}` ends the cast; the token dies at once.
- `GET|HEAD /cast/media/{kind}/{view_id}/still.png?token=...` is what the TV
  fetches. AuthMiddleware exempts exactly the `/cast/media/` prefix for
  GET/HEAD; the route is gated solely by the cast token: unknown, expired or
  revoked -> 401, a token for another view -> 403, a token bound to a
  `device_address` fetched from another address -> 403.

device_address binding compares the TCP peer, so it only holds when the TV reaches taOS directly on the LAN listener; behind a local reverse proxy (tailscale serve, the gateway listener) the peer is the proxy and a bound cast is refused (fails closed).

The token (`tcast_...`) is a separate random secret stored only as a SHA-256
hash, in memory (a restart revokes every cast). It is never a session token or
a registry JWT and is accepted by nothing but the cast media route. The
paired-device registry's unpair MUST call
`app.state.cast_tokens.revoke_for_device(device_id)`; cast-end revocation alone
does not cover a device unpaired mid-cast.
