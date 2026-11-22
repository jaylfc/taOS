### Fixed

- Cluster: enable the Drain button in the Cluster app. Draining a worker POSTs to /api/cluster/workers/<name>/drain with {graceful: true} after a confirmation, and a draining worker shows Cancel drain calling /api/cluster/workers/<name>/cancel-drain. The button stays hidden for device kind workers. Restart remains disabled since no backend route exists.
