# Desktop nitpick batch

- Fix `AbortSignal.timeout(0)` to schedule the first step with `setTimeout(step, 0)` so listeners registered after the call receive the abort event
- Polyfill install test: assign stub before calling `installAbortSignalTimeout()` and assert exact stub is preserved
- WifiPanel: keep secured connect POST pending, assert Join button shows "Connecting" while in flight
- WifiPanel: assert Show password button has numeric `tabIndex >= 0`
- WifiPanel: add test that tabs through open password prompt and asserts Cancel receives focus
- Onboarding model: assert `HopError` type and message on poll-limit rejection