# LoRA Studio routes (session-only, no agent scope)

<!-- Route module `tinyagentos/routes/lora_studio.py`. These are OWNER routes: they sit behind the session cookie plus the CSRF double-submit on writes, and no registry scope reaches them -->

## API endpoints

### POST /api/loras/ingest

- Form field `url`, a `civitai.com` / `civitai.red` model page
- Answers `202` with the pending row; the download runs in a background task
- `400` for any other host or an unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row; `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview image `n`
- Path re-checked against the archive root before serving

### DELETE /api/loras/{id}

- Removes the row, the safetensors file and the LoRA directory
- `400` rather than a delete if a stored path resolves outside the archive root

### POST /api/loras/{id}/retry

- Re-runs a `failed` ingest
- The `failed → pending` transition is one atomic UPDATE: concurrent retries get one `202` and one `409`, never two download jobs in one directory

## Archive layout

- Files land under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, so adapters never appear as loadable models