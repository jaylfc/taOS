### Added
- Extended `recommended_framework(ram_mb, device_class=None)` to return "picoclaw" for mobile device class (taOSmobile handsets) regardless of RAM, in addition to the existing <=8 GB RAM rule.
- Added `device_class` field to `HardwareProfile` with detection via `taos-kiosk.service` systemd unit presence (capability probe matching the session-mode API).
- Hardware profile API (`/api/hardware`) now exposes `device_class` field.
- DeployWizard test verifying that PicoClaw recommendation is never forced — users can select a different framework and the deploy request carries their choice.