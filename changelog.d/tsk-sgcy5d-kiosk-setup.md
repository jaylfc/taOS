### Fixed

- kiosk-setup.sh: refreshes the apt index unconditionally, installs and enables
  `seatd`, and adds the kiosk user to the `seat` group even when `seatd` was
  already installed — failing loudly instead of silently degrading
- taos-kiosk.service: `Wants=tinyagentos.service seatd.service` is now one valid
  assignment (the old `Wants=… Wants=…` line dropped the seatd dependency)
- docs/kiosk-setup.md: new page covering kiosk setup
