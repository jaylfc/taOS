### Fixed
- Keenable switch now uses strict boolean check (`=== true`) so stored "false" or 1 no longer incorrectly shows the switch as ON
- On failed initial preference load, an inline alert is shown with a Retry button (aria-label "Retry loading Keenable preference"); the switch is disabled until a successful retry loads the preference
