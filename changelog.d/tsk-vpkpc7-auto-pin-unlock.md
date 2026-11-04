### Fixed
- PIN unlock auto-submits when the correct length is entered; Sign in with PIN button shown only when length unknown; PIN length stored/learned on success.
- The lock screen rendered `data-pin-length` from an undefined `auth_mgr` inside `_login_page`, so the auto-submit-on-last-digit feature was dead and the PIN panel always fell back to a submit button. The length now arrives as a keyword argument from `login_page`, which already has the `AuthManager` in scope.
- `AuthManager.pin_length()` clamps to the 4..12 PIN range (an out-of-range stored value returns `None` instead of leaking through), verifies the PIN hash before saving a learned length backfill, and logs a `warning` on a failed backfill instead of passing silently.
- The wrong-PIN node test now asserts the 401 status the panel received, not just that a post was made.
