# Config save and restore (`/api/config`, session-only)

<!-- Route module `tinyagentos/routes/settings.py`. Owner routes behind the session cookie plus the CSRF double-submit on writes; no registry scope reaches them -->

## API endpoints

### GET /api/config

- `{"yaml": "<serialised AppConfig>"}`

### PUT /api/config

- Body: `{"yaml": "..."}`
- Optional `?validate_only=true` to check without saving
- Answers `400` with `details` when validation fails

### POST /api/restore

- Multipart `file`, restores a backup tarball into the data dir
- **The path is `/api/restore`, NOT `/api/settings/restore`**, even though the handler sits in `routes/settings.py` beside the `/api/settings/*` routes
- Upload capped at 64 MB, refused with `413` while the body is still arriving (`tinyagentos/middleware/upload_body_limit.py`, since FastAPI spools a multipart file before the handler runs); the tarball goes through `tinyagentos/safe_archive.py`, so over the shared bomb caps (256 MB declared uncompressed, 64 MB per member, 10000 members) or carrying a member the path-safe tar filter rejects, the restore answers `400` and writes nothing

## Important: both write paths REBUILD `AppConfig` field by field

- A field missing from either rebuild is silently dropped on the next save, wiping whatever the user had set
- Has happened twice already: `archive`, `archived_agents` and `github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374)
- Adding a field to `AppConfig` means adding it at BOTH sites in this module
- `test_save_config_preserves_all_to_dict_keys` compares the whole `to_dict()` key set against what survives a round trip and fails if one is forgotten
- Never fix such a leak by removing the field from `to_dict()`: `save_config()` serialises from there, so that makes the setting unpersistable
