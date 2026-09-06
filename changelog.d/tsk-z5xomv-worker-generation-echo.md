### Fixed: split-brain layer-2 protection is now actually reachable

- The controller echoes its current generation in both POST /api/cluster/workers
  (register) and POST /api/cluster/heartbeat responses, and WorkerAgent captures
  and returns it on every subsequent request. Previously the worker-side capture
  read a field no response contained, so the layer-2 guard (manager.py:110-115,
  294-299) could never fire. A missing echo is now a logged warning, never a
  silent downgrade. End-to-end test: a heartbeat carrying a stale generation is
  rejected 404.
