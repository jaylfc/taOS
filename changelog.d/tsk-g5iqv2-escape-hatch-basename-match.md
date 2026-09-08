### Fixed

- **`Tests-Skipped-Intentionally` trailer now accepts full repo paths as well as bare basenames**: `has_escape_hatch()` in `.github/scripts/check_all_skip.py` now compares the basename of the claimed file against the file's basename, so trailers written with paths like `tests/taosnet/test_torrent_downloader_taosnet.py, why` are correctly matched. The defence against suffix spoofing (`test_x.py.bak` cannot waive `test_x.py`) is preserved.
