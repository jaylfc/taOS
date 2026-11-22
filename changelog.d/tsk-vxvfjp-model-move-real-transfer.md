### Added
- Real model transfer between cluster workers over authenticated worker channel (`/api/cluster/models/move`): target worker pulls model files from controller's model store, verifies checksums, checks disk space, loads model, then source worker unloads; progress tracked via job ID; agent pins updated only after successful completion
