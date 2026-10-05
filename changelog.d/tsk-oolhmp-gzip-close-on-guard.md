### Fixed

- Ensure the GzipFile is closed when `tarfile.open` raises an exception other than ReadError, BadGzipFile, or EOFError in `open_tar_gz`.
