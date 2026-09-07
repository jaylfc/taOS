### Fixed

- the `.claim` sidecar now records `<pid> <boot_id>` and is reclaimed only when the owner is proven dead; a live-but-slow writer is never preempted