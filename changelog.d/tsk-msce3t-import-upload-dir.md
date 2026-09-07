### Fixed
- `/api/import/upload` and `/api/import/embed` now place files under `<data_dir>/imports/uploads` instead of a predictable `/tmp` path, and refuse requests when the upload directory is a symlink, not a directory, or not owned by the service user.
