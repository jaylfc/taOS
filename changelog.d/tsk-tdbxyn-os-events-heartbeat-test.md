### Fixed

- Strengthened `use-os-events` heartbeat watchdog test to capture the EventSource
  instance before advancing time and assert on that instance directly, proving
  the watchdog is re-armed by incoming heartbeats and closes the stream only
  after the full deadline with no heartbeat.
