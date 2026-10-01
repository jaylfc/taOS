### Fixed

- NotificationsApp now passes `harness` through to ConsentActions so the Grok shared-VM warning is visible when a Grok auth-request notification is opened from the Notifications app.
- Join-kit README marks the credential-file storage steps as non-Grok and points Grok readers to the secure form plus environment-variable client config.
- Grok invite guide text in `project_invites.py` (and its docs mirror) now correctly distinguishes:
  - Grok block: polling the status endpoint is for ONBOARDING (retrieving the token), and ongoing messages and @mentions arrive on the A2A bus, with ready tasks found by the timed check of `tasks/ready`.
  - NON-Grok closing paragraph: the timed check is the reliable delivery floor and the stream is an optional optimization (RESTORED original wording; PR #3332 incorrectly changed it).
