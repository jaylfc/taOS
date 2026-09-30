### Fixed

- **Security:** Fixed cluster node DELETE to fail closed when the pairing store is unavailable (tsk-khpvzn: cluster-node-delete-fix)
  - DELETE /api/cluster/workers/{name} now returns 503 with "STORE_UNAVAILABLE" error when the pairing store is unavailable
  - The worker row is no longer deleted when the store is unavailable, preventing deleted nodes from still authenticating
  - The pairing key is always revoked when the store is available, ensuring deleted nodes cannot authenticate with their old key

- **Tests:** Fixed test harness to properly initialize ClusterPairingStore
  - Added `_ensure_cluster_pairing_store` fixture to initialize the store for the test client
  - Added `TestClusterAdminDeleteWithoutPairingStore` tests to verify the fix works correctly
