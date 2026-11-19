### Fixed
- Backend recovery now triggers reload: `onUp` callback wired in `BackendStatusContext` to dispatch `taos:backend-up` and reload the page when the health probe succeeds after reconnecting.
- Added a provider-level test (real provider, real singleton, mocked global fetch and `location.reload`) verifying `taos:backend-up` and the reload each fire exactly once on recovery.
- Removed the provider-and-banner-only mobile tests (they did not exercise the fix) and the duplicate backoff-cap test; the visibility tests restore `document.visibilityState` in a `finally`.
