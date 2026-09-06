### Fixed

- Model downloads no longer hang forever on a stalled connection. The HTTP
  transfer in `tinyagentos/download_manager.py` ran with `timeout=None`, which
  disables the connect, read, write and pool timeouts together: a Wi-Fi drop, a
  NAT table eviction or a CDN edge that stopped sending left the task at
  `status="downloading"` with nothing ever erroring, showing a progress bar
  frozen part-way with no way to tell it apart from a slow link. It now uses
  finite timeouts and retries transport errors and 5xx responses with
  exponential backoff, so a single transient failure from a mirror no longer
  kills a multi-gigabyte transfer. Expect previously invisible stalls to start
  surfacing as errors — that is the fix working.
- Interrupted model downloads resume instead of restarting. Bytes already on
  disk are asked for with a `Range` header, so a 40 GB model that fails at
  39 GB continues from where it stopped; a server that ignores the header and
  answers `200` restarts cleanly rather than appending a second copy.
- A failed model download no longer leaves a corrupt file at the canonical
  path, where every later "is this model installed?" existence check would take
  it for a real weight. Bytes are staged in a `<dest>.part` file and renamed
  onto the destination only after validation passes.
- Finished download tasks are pruned after an hour instead of staying resident
  for the lifetime of the process, so `/api/models/downloads` no longer grows
  without bound. Pending and downloading tasks are never pruned.
- A re-download of an already-installed model no longer deletes the existing
  valid file when the new attempt fails before promoting anything: the
  cleanup on failure now only removes `task.dest` when this attempt actually
  renamed the `.part` stage file onto it.
