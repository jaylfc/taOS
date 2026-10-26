### Added

- Added Bluetooth write routes for adapter power and device operations (connect/disconnect/pair/trust/untrust/remove) over busctl, with path validation and error handling

### Fixed

- Path validation now prevents injection attacks before any busctl calls are made
- Added proper error handling for bluez_unavailable (rc 127), timeout (rc 124), and write_failed (rc 1) scenarios
- Device operations now use the correct D-Bus method signatures per BlueZ specification