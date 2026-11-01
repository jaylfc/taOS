# Hardware controls (Wi-Fi, Bluetooth)

<!-- Wi-Fi routes in `tinyagentos/routes/hardware_wifi.py`, Bluetooth routes in `tinyagentos/routes/hardware_bluetooth.py` -->

## API endpoints

### GET /api/hardware/wifi

- Response: `available`, `wifi_enabled`, `devices[]`, `access_points[]`
- `available=false` = NetworkManager could not be queried (HTTP 200; only the write routes return 503)

### POST /api/hardware/wifi/enabled

- Body: `{enabled: bool}`; success: `{"ok": true, "wifi_enabled": bool}`
- 503 `networkmanager_unavailable`; 502 `write_failed` (polkit)

### POST /api/hardware/wifi/disconnect

- Body: `{iface: str}`; success: `{"ok": true}`
- 503 `networkmanager_unavailable`; 404 `unknown_iface`; 502 `write_failed` (polkit)

### POST /api/hardware/wifi/connect

- Body: `{ssid, psk?, iface?}`; PSK via dbus-fast in-process, never on a busctl argv
- 503 `networkmanager_unavailable` / `dbus_fast_missing`
- 422 `invalid_ssid` / `invalid_psk`; 404 `no_wifi_device`
- 403 `not_permitted` (polkit); 502 `connect_failed`

### POST /api/hardware/wifi/scan

- Body: `{iface?}`; triggers RequestScan on one or all Wi-Fi devices
- 202 `{"ok": true, "scanning": [<iface>...]}` on success
- 503 `networkmanager_unavailable`; 404 `unknown_iface`; 502 `scan_failed`

### POST /api/hardware/wifi/forget

- Body: `{ssid}`; deletes matching 802-11-wireless system profiles by exact SSID byte match
- 422 `invalid_ssid` if SSID not 1..32 bytes UTF-8; zero busctl calls
- Success: `{"ok": true, "removed": <n>}`; 404 `unknown_ssid` if no match
- 502 `write_failed` (polkit) if Delete fails; never calls GetSecrets

### GET /api/hardware/bluetooth

- Response: `available`, `adapters[]`, `devices[]`
- `available=false` = BlueZ could not be queried (HTTP 200; only the write routes return 503)
- Each adapter includes `path`, `address`, `powered`, `discovering`

### POST /api/hardware/bluetooth/power

- Body: `{adapter, powered}`; success: `{"ok": true}`
- 422 `invalid_path`; 503 `bluez_unavailable`; 504 `timeout`; 502 `write_failed`

### POST /api/hardware/bluetooth/discovery

- Body: `{adapter, on: bool}`; starts or stops discovery on the adapter
- On `on=true`: calls `Adapter1.StartDiscovery`, schedules `StopDiscovery` after 60 s, returns `{"ok": true, "discovering": bool}`
- On `on=false`: cancels timer, calls `Adapter1.StopDiscovery`, returns `{"ok": true, "discovering": false}`
- `org.bluez.Error.InProgress` on start counts as success
- 422 `invalid_path`; 503 `bluez_unavailable` / `dbus_fast_missing`; 502 `write_failed` (other D-Bus error)
- 504 `bluez_timeout` on D-Bus call timeout; 502 `bluez_error` on StopDiscovery D-Bus error

### POST /api/hardware/bluetooth/device

- Body: `{path, action: connect|disconnect|pair|trust|untrust|remove}`
- 422 `invalid_path`; 400 `invalid_action`
- 503 `bluez_unavailable`; 504 `timeout`; 502 `write_failed`

All routes admin-only via `Depends(require_admin)`.
