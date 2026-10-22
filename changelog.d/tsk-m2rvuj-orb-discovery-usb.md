### Added

- USB Orb discovery module (tinyagentos/orb/discovery.py):
  - Lists attached taOS Orbs by USB VID
  - Imports pyserial lazily inside list_usb_orbs()
  - Raises TransportUnavailable with clear installation instructions if pyserial not installed
  - Returns a list of dicts with keys: port, vid, pid, serial_number, description
  - Sorts results by port name
  - Skips ports with None VID without error