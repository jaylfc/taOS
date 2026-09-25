### Fixed

- Default-deny admin/owner dependency on every mutating global router that previously carried none, closing the authz gap where AuthMiddleware authenticated but did not authorize.
- validate_session now rejects a session that has a stored User-Agent hash when the caller omits the User-Agent header (missing UA is a mismatch, not a bypass).
- change_password now revokes all other sessions for the user after a successful password change, matching admin_reset_password behaviour.
