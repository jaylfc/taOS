### Changed

- `approve_request_record` now reads `duration_secs` from the auth-request record and passes
  a computed `expires_at` to every `add_grant` call, so time-boxed grants actually expire.
  Previously every approval created permanent grants even when `duration_secs > 0`. (taOS #2985)
