### Fixed

- Archive writes are now lossless: failed `archive.record()` calls are spooled durably to `<data_dir>/archive/spool.jsonl` with full fidelity (no stripping/truncation) and replayed at startup and every 5 minutes via a background drain task. Affected sites: `routes/chat.py`, `channel_hub/router.py`, `bridge_session.py`.