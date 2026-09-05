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

1. Refresh the apt package index
2. Install `cage` (Wayland compositor) if not present
3. Install `seatd` (seat driver), run `systemctl enable --now seatd`, and add the kiosk user to the `seat` group — creating that group first if the `seatd` package did not
4. Install `chromium-browser` if not present
5. Generate `/etc/systemd/system/taos-kiosk.service` with the seatd dependency declared
6. Create the `taos-kiosk` convenience script

Steps 3 and 4 run whenever `seatd` is available, including when it was already installed before the script ran. If enabling `seatd` or the group update fails, the script exits non-zero rather than reporting a successful setup.

## Service

The generated `taos-kiosk.service` declares:

```ini
After=tinyagentos.service network-online.target seatd.service
Wants=tinyagentos.service seatd.service
```

so the kiosk waits for the seat driver before starting. Both units belong to a single `Wants=` assignment — systemd splits one assignment on whitespace, and a second `Wants=` token inside the value is not a valid unit name, so it would be dropped. The service runs `cage` as the Wayland compositor to boot Chromium in kiosk mode.

## Notes

- On systems without `seatd`, the kiosk service may fail with `'Could not activate session: Interactive authentication required'`. Installing and starting `seatd` resolves this.
- The kiosk user should be a member of the `seat` group for proper input device access. Group membership only takes effect on the next login, so reboot after running the setup script.

## Troubleshooting

If the kiosk fails to start with "Interactive authentication required":

```bash
systemctl status seatd          # must be active
sudo systemctl enable --now seatd
id "$USER"                      # must list the seat group
```

The setup script performs both of these, so a failure here usually means the script was not run as root or exited early — re-run `sudo bash scripts/kiosk-setup.sh` and check its output.