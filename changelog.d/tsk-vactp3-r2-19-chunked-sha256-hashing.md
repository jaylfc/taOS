### Fixed
- SHA-256 verification in `DownloadManager._validate_download` and `TorrentDownloader.download` now streams files through `hashlib.file_digest` instead of loading the entire model file into RAM with `read_bytes()`. This prevents multi-GB model downloads from exhausting host memory on 4 GB devices.
