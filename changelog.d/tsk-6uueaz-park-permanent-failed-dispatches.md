### Added

- Dispatcher now permanently parks a card after `STRIKE_THRESHOLD` cumulative failed dispatches (releases), instead of quarantining it. Strikes are cumulative with no time window.
