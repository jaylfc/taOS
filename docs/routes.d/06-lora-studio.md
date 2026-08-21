# LoRA Studio routes (session-only, no agent scope)

<!-- Route module `tinyagentos/routes/lora_studio.py`. These are OWNER routes: they sit behind the session cookie plus the CSRF double-submit on writes, and no registry scope reaches them -->

## API endpoints

### POST /api/loras/ingest

- Form field `url`, a `civitai.com` / `civitai.red` model page
- Answers `202` with the pending row and runs the download in a background task
- `400` for any other host or an unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row, `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview image `n`
- Paths are re-checked against the archive root before the file is served

### DELETE /api/loras/{id}

- Removes the row, the safetensors file, and the LoRA directory
- Refuses with `400` if a stored path resolves outside the archive root rather than deleting it

### POST /api/loras/{id}/retry

- Re-runs a `failed` ingest
- The `failed → pending` transition is a single atomic UPDATE, so concurrent retries get one `202` and one `409`, never two download jobs in one directory

## Archive layout

- Files land under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, so adapters never appear as loadable models