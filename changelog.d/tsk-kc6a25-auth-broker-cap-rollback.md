### Fixed
- AuthBrokerStore.create now rolls back the transaction when the per-agent cap rejects, preventing a dangling write lock and uncommitted nulled codes.