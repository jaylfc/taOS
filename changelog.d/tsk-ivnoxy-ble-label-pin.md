### Added
- BLE pairing: the controller now reads the board's label PIN from the info characteristic and surfaces it to the admin. `POST /api/cluster/ble/pair/confirm` accepts a `label_pin` field; a wrong PIN is refused with `403` and rolls back exactly as a rejection does. The Cluster app's Bluetooth pairing dialog asks for the PIN and explains where to find it.
