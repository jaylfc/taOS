### Fixed

- An invited user can now finish setting up their account in the app. The completion screen never had the invite code (the status call does not return it), so it showed the first-run form instead and its submit failed with "already configured". `/auth/complete` now accepts the invitee's own pending session, the one their invite code opened, in place of the code, and checks the CSRF token on that path itself; any other session is refused and a supplied code must still match.
- The first-run and invite password fields now ask for at least 8 characters, matching what the server requires, instead of accepting 4 and failing on submit.
