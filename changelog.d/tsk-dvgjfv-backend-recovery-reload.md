### Fixed
- Backend recovery now triggers reload: `onUp` callback wired in `BackendStatusContext` to dispatch `taos:backend-up` event and reload page when health probe succeeds after reconnecting.
- Added provider-level test verifying `taos:backend-up` fires exactly once on recovery.
- Replaced vacuous `MobileShellHomeAction` tests with integration tests rendering mobile shell with reconnecting banner.
- Removed duplicate backoff-cap test; restore `document.visibilityState` after visibility tests.
