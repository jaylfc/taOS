### Fixed
- Backend recovery now refetches instead of reloading: `onUp` is wired in `BackendStatusContext` to dispatch `taos:backend-up` and bump a `recoveredAt` counter when the health probe succeeds after reconnecting. The page is never reloaded; apps that opt in via `useOnBackendRecovered` re-run their loader.
- Added a provider-level test (real provider, real singleton, mocked global fetch) verifying `taos:backend-up` fires exactly once on recovery and `location.reload` is never called.
- Removed the provider-and-banner-only mobile tests (they did not exercise the fix) and the duplicate backoff-cap test; the visibility tests restore `document.visibilityState` in a `finally`.
