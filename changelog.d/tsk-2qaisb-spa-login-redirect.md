### Fixed: the touchscreen kiosk now reaches the PIN sign-in screen

- The desktop SPA no longer renders a sign-in form of its own. When
  `/auth/status` reports the install is configured but the visitor is not
  authenticated, `LoginGate` hands off to the server-rendered `/auth/login`,
  carrying the current path as `next` so the user returns where they started.
- This closes a split that had teeth on a keyboard-less device. `/desktop` is in
  `EXEMPT_PATHS`, so the session gate at `auth_middleware.py:636` never fires for
  the shell HTML: the kiosk booted straight into `LoginGate`'s password-only form
  and never saw the PIN keypad added on `/auth/login`. On a touchscreen Pi with no
  keyboard that is a hard lockout — the device cannot be signed into from its own
  screen. Reproduced on the real pitop kiosk before and after.
- `auth_middleware.py:285-292` already documented this handoff as the contract;
  `LoginGate` had simply drifted away from it. The invite flow is unaffected —
  `POST /auth/login` creates a session for a pending user and returns them to
  `/desktop`, where `/auth/status` reports `needs_onboarding` and the SPA renders
  the completion screen, exactly as `routes/auth.py` already described.
- A repeat redirect is guarded: if the SPA comes back from `/auth/login` still
  unauthenticated it stops and offers a link instead of bouncing between two URLs
  forever, which on a kiosk would be worse than any login form.
