### Fixed

- WifiPanel follow-up (#4122 CR):
  - Join button now shows "Connecting" spinner only when `isConnecting && ap.secured && submitting`
  - When secured prompt is open and idle (`isConnecting && ap.secured && !submitting`), shows plain "Join" button
  - Show password button removed `tabIndex={-1}` for keyboard accessibility
  - Cancel button removed `tabIndex={-1}` for keyboard accessibility
  - Empty password on Enter now does nothing (Join button remains disabled)
  - Secured AP prompt shows "Enter the network password." error without making API call when password is empty
  - `handleToggleWifi` and `handleDisconnect` now `await load()` after successful POST to refresh the list before clearing `toggling`/`disconnecting` states
  - User can now access Show password/Cancel buttons with keyboard navigation