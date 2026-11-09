### Fixed
- AbortSignal.timeout(0) now defers via setTimeout so listeners registered immediately after are not missed
- WifiPanel: secured connect in-flight shows "Connecting" on the Join button; Show password button has tabIndex >= 0; tab navigation through password prompt reaches Cancel button
- Onboarding model poll-limit error now asserts HopError type and message