# Kiosk Mode

Run taOS directly in kiosk mode, booting straight into a fullscreen Chromium session pointing at the taOS desktop.

## Prerequisites

- `cage` — minimal Wayland compositor
- `seatd` — seat driver for kiosk authentication (required for wlroots backends)
- `chromium-browser` or `chromium`

## Installation

Run the kiosk setup script as root:

```bash
sudo bash scripts/kiosk-setup.sh
```

This script will:

1. Install `cage` (Wayland compositor) if not present
2. Install `seatd` (seat driver) and add the kiosk user to the `seat` group
3. Install `chromium-browser` if not present
4. Generate `/etc/systemd/system/taos-kiosk.service` with the seatd dependency declared
5. Create the `taos-kiosk` convenience script

## Service

The generated `taos-kiosk.service` includes `After=seatd.service` and `Wants=seatd.service` so the kiosk service will wait for the seat driver before starting. The service runs `cage` as the Wayland compositor to boot Chromium in kiosk mode.

## Notes

- On systems without `seatd`, the kiosk service may fail with `'Could not activate session: Interactive authentication required'`. Installing and starting `seatd` resolves this.
- The kiosk user should be a member of the `seat` group for proper input device access.

## Troubleshooting

If the kiosk fails to start with "Interactive authentication required", ensure `seatd` is installed and running, and the kiosk user is a member of the `seat` group.