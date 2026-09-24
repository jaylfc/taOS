### Added

- Cluster: the controller can now pair a taOSusb board over Bluetooth (S1).
  `GET /api/cluster/ble/scan`, `POST /api/cluster/ble/pair/start`,
  `POST /api/cluster/ble/pair/confirm` and `POST /api/cluster/ble/pair/cancel`
  drive a BLE GATT handshake against the board, show a 6-digit confirmation
  code, and mint the node's signing key through the same path manual worker
  pairing uses. The browser never sees the key, an LLM credential, or a mesh
  preauth. Requires the optional `ble` extra (`bleak`); without it or an
  adapter, the routes answer 503 `bluetooth_unavailable`.
- Cluster workers gain a `kind` field (`worker` or `device`). A BLE-paired
  board registers as `kind="device"` and is excluded from every placement
  path that picks a job candidate -- chat/embed/image-generation routing and
  browser-session placement -- regardless of what it advertises in
  `capabilities`. Existing rows default to `kind="worker"` via a guarded
  migration.
