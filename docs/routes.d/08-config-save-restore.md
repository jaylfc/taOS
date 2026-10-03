# Config save and restore (`/api/config`, session-only)

<!-- Route module `tinyagentos/routes/settings.py`. Owner routes behind session cookie + CSRF double-submit on writes; no registry scope reaches them -->

## API endpoints

### GET /api/config

- `{"yaml": "<serialised AppConfig>"}`

### PUT /api/config

- Body: `{"yaml": "..."}`
- Optional `?validate_only=true` to check without saving
- `400` with `details` on validation failure

### POST /api/restore

- Multipart `file`, restores backup tarball to data dir
- **Path is `/api/restore`, NOT `/api/settings/restore`** (handler in `routes/settings.py`)
- Upload capped 64 MB, refused `413` while body arriving (`upload_body_limit.py`); tarball via `safe_archive.py` — over bomb caps (256 MB declared, 64 MB/member, 10000 members) or rejected by path-safe tar filter → `400`, writes nothing

## Important: both write paths REBUILD `AppConfig` field by field

- Missing field in either rebuild = silently dropped on next save, wiping user setting
- Happened twice: `archive`, `archived_agents`, `github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374)
- Adding field to `AppConfig` = add at BOTH sites in this module
- `test_save_config_preserves_all_to_dict_keys` compares `to_dict()` key set vs round-trip, fails if forgotten
- Never fix leak by removing from `to_dict()`: `save_config()` serialises from there, making setting unpersistable