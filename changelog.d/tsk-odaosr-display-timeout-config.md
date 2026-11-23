### Added
- taos-kiosk-idle now reads SCREEN_OFF_SECONDS from /etc/taos/display.conf (shared with phone layer), with TAOS_DISPLAY_CONF override for tests. Valid values: 0 (never), 5..3600. Missing file uses default 30; malformed/out-of-range falls back to 30 with a warning. File wins when present and valid; TAOS_KIOSK_IDLE_SECS env override applies only when file is absent or invalid.
- install-session.sh installs session/etc/display.conf (SCREEN_OFF_SECONDS=60) to /etc/taos/display.conf only if missing, so a chosen value survives updates.
- New test suite tests/session/test_taos_kiosk_idle.py covering valid/fallback values, env override precedence, no-sourcing guarantee, and install idempotency.
