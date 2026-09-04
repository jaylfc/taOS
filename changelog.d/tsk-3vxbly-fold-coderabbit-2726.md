### Fixed
- Path traversal (CWE-22) in model archive promotion: a crafted `model_id` such as `../victim` can no longer make `model_files_dir` resolve outside the archive root. The promotion engine now validates `model_files_dir` against `archive_root_path` before moving files.
- Registration payloads with `"ram_mb": null` no longer propagate `None` into `WorkerInfo.hardware`, preventing `TypeError` from `ram_mb // 1024` in `worker_tier_id` when `list_workers` is called.
