### Added

- Password reset by email: POST /api/password/request initiates a password reset flow (mints token, stores SHA-256 hash with 30-min TTL, sends email via email connector); GET /api/password/reset validates and consumes token atomically, sets new password without requiring current password, and revokes all user sessions.