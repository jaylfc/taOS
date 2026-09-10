### Fixed
- Restore the `_upload_path` docstring and `embed_files` traversal comment (GHSA-rwrp-hfc4-qg2w rationale) lost in #2895.
- Log each upload-dir refusal in `_ensure_upload_dir` so operators see symlink, not-a-directory, and wrong-owner 500s in the journal.
- Refuse dangling upload-dir symlinks explicitly by using `os.lstat` instead of `Path.exists()`.
