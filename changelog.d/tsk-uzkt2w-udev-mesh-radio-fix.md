### Fixed
- udev rule for LoRa mesh radios now uses the correct header matching the desktop spec, installs with explicit 0644 mode, triggers udev on install/uninstall so plugged radios get access without replug, and the phone rule is byte-identical to the desktop source.
