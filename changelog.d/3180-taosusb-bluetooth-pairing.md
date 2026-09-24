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
- Cluster app: **Add device → Bluetooth** lists nearby taOSusb boards, shows
  the 6-digit pairing code to compare with the board, and pairs on confirm.
  Paired boards appear as devices with job and capacity controls hidden.
- The Bluetooth scan recognises a taOSusb board by its manufacturer-data
  marker as well as its service UUID, and lists a board that is already
  paired straight from its advert, without connecting to it.
- With the LLM gateway on (`TAOS_LLM_GATEWAY=1`), pairing a board also mints
  its model key, bound to the node and allowed `taos-default`, and seals
  `llm: {base, key}` into the provision sent to the board. The key never
  appears in an HTTP response or a log. It is revoked if the board rejects
  the provision, and with the node through the existing revoke, block and
  delete routes. With the gateway off the board gets `llm: null`.
