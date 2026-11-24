# OS notifications: `POST /api/notifications/os`

Where the desktop session's notification daemon (`taos-notifyd`) posts each freedesktop `Notify` it accepted.

- Caller: the signed-in person's session (or the local token, which maps to the primary person), on a loopback connection with no forwarding header (else 403 `loopback_only`). The recipient is always the caller; a `user_id` field is refused.
- Body (JSON, at most 16 KiB, else 413; malformed JSON 400; bad fields 422): `id` (client-chosen, 1 to 64 of `A-Za-z0-9._:-`), `app_name` (128), `desktop_entry` (128), `summary` (256), `body` (2048), `urgency` (`low`/`normal`/`critical`), `category`, `icon` (256, name or path), `actions` (at most 8 of `{key, label}`), `replaces` (an earlier `id` to update in place; if that row is gone a new row is stored), `expire_timeout_ms`, `transient` (accepted, not stored), `posted_at` (epoch seconds, clamped to the last 30 days).
- Answer: `{"id": <row id>, "duplicate": bool}`. The same `id` from the same person with the same content is stored once and answers `duplicate: true`, even after the row was read or dismissed. The same `id` with different content answers 409 `id_reused_with_different_content` and changes nothing. A muted source answers `{"id": null, "stored": false, "reason": "muted"}`.
- Row: source `os:<desktop entry or app name>`, level `info` (`warning` for critical), details under `data.os`. No webhook fires.
- Id records live as long as their rows: retention (`cleanup`) prunes them together. A daemon retry can only outlive its record after 30 days of failed delivery, and then yields one fresh row that ages out again.
- Limit: 120 posts a minute per person, then 429 with `Retry-After`.
