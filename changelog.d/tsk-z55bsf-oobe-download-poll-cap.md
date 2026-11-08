### Fixed

- waitForDownload now caps consecutive failed polls at 20, throwing HopError instead of looping forever when the download endpoint returns non-404 errors (e.g., 500).