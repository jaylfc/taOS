### Added

- First-run onboarding now offers an optional "Your taOS account" step after the local account: sign in to or create a taos.my account, or skip and keep everything on this device. Closed sign-ups show a plain notice with Skip still available.

### Fixed

- Signing in to a taOS account (Settings, Account, and the new onboarding step) reported "Unexpected response" on success: taos.my answers login with only `{user_id}`, so the client now reads the account from `/api/account/me`. Create account now signs in straight after registering, a two-step sign-in answer is reported instead of read as success, and taos.my error codes (`invalid_credentials`, `signups_closed`, ...) show readable text.
