### Fixed

- Concurrent `AgentRegistryStore.register()` calls with the same slug and second no longer 500 on `IntegrityError`; the insert is retried with a `-NN` suffix up to 16 times.
