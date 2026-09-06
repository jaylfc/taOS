### Fixed

- A corrupt model re-download no longer destroys an existing valid install.
  `DownloadManager._download` promoted the `.part` stage file onto `task.dest`
  before running SHA256 and size validation; when the re-download's bytes
  mismatched, the good file at `task.dest` was first overwritten and then
  deleted, leaving the user with nothing. Validation now runs against the
  `.part` file first, and only on success is it atomically renamed onto
  `task.dest`. A mismatch unlinks the `.part` alone and leaves `task.dest`
  untouched. The `promoted` flag that tracked whether `task.dest` had been
  overwritten is no longer needed and has been removed.
- `_validate_download` now accepts a `path` parameter so callers can validate
  either the `.part` stage file or `task.dest` without re-reading a file whose
  streamed digest is already in hand.
- The torrent path in `_download_with_fallback` already validates before
  marking a task complete (it writes directly to `task.dest` and
  `torrent.download()` verifies SHA internally before returning), so the
  promote-before-validate defect never applied there.
