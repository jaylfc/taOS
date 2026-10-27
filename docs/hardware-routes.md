# Hardware controls (Wi-Fi, Bluetooth)

<!-- Wi-Fi routes in `tinyagentos/routes/hardware_wifi.py`, Bluetooth routes in `tinyagentos/routes/hardware_bluetooth.py` -->

## API endpoints

### GET /api/hardware/wifi

- Response: `available`, `wifi_enabled`, `devices[]`, `access_points[]`
- `available=false` = NetworkManager unreachable (503)

### POST /api/hardware/wifi/enabled

- Body: `{enabled: bool}`; success: `{"ok": true, "wifi_enabled": bool}`
- 503 `networkmanager_unavailable`; 502 `write_failed` (polkit)

### POST /api/hardware/wifi/disconnect

- Body: `{iface: str}`; success: `{"ok": true}`
- 503 `networkmanager_unavailable`; 404 `unknown_iface`; 502 `write_failed` (polkit)

### POST /api/hardware/wifi/connect

- Body: `{ssid, psk?, iface?}`; PSK via dbus-fast in-process, never on a busctl argv
- 503 `networkmanager_unavailable` / `dbus_fast_missing`
- 422 `invalid_ssid` / `invalid_psk`; 404 `no_wifi_device` / `unknown_iface`
- 403 `not_permitted` (polkit); 502 `connect_failed`

### GET /api/hardware/bluetooth

- Response: `available`, `adapters[]`, `devices[]`
- `available=false` = BlueZ unreachable (503)

### POST /api/hardware/bluetooth/power

- Body: `{adapter, powered}`; success: `{"ok": true}`
- 422 `invalid_path`; 503 `bluez_unavailable`; 504 `timeout`; 502 `write_failed`

### POST /api/hardware/bluetooth/device

- Body: `{path, action: connect|disconnect|pair|trust|untrust|remove}`
- 422 `invalid_path`; 400 `invalid_action`
- 503 `bluez_unavailable`; 504 `timeout`; 502 `write_failed`

All routes admin-only via `Depends(require_admin)`.
