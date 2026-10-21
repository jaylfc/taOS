### Fixed
- app-standalone-main now bounds the device-class prime wait to 2 seconds so a hung config fetch cannot block standalone app render, preventing blank windows.
- Added test for when config fetch never resolves.