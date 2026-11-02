### Added
- Wi-Fi panel radio toggle, Join with inline password field (show/hide, Enter to submit, Esc to cancel), and Disconnect button using existing write routes
### Fixed
- Open-network join errors now surface as a global error and clear the Connecting status and Join spinner instead of being swallowed by the inline password block
- Secured Wi-Fi join now has a visible Join button in the password prompt for touch-only users
- Toggle and Disconnect error paths now clear the in-flight status text
- Toggle and Disconnect buttons disabled while in-flight to prevent double-submit
- Cancel button in password prompt disabled while submitting
- Inline password error announced via role=alert with stable ID for aria-describedby