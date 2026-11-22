### Fixed

- **Cluster: model move is now a real transfer over the worker channel.** `POST /api/cluster/models/move` starts a tracked job that stages the source's model file into the target worker's store, verifies the checksum against the source's recorded hash, refuses the copy when the target lacks disk space, waits for the target's heartbeat to report the model before unloading the source, cleans up staged files on failure, and updates agent pins only after success.
