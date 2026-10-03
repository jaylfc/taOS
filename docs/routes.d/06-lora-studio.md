# LoRA Studio routes (session-only, no agent scope)

<!-- Route module `tinyagentos/routes/lora_studio.py`. OWNER routes: behind session cookie + CSRF double-submit on writes, no registry scope reaches them -->

## API endpoints

### POST /api/loras/ingest

- Form `url`: `civitai.com` / `civitai.red` model page
- `202` with pending row; download in background
- `400` for other host/unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row; `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview `n`
- Path re-checked against archive root before serving

### DELETE /api/loras/{id}

- Removes row, safetensors file, LoRA directory
- `400` if stored path resolves outside archive root

### POST /api/loras/{id}/retry

- Re-runs `failed` ingest
- `failed → pending` is atomic UPDATE: concurrent retries get one `202`, one `409`, never two jobs in one dir

## Archive layout

- Files under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, adapters never appear as loadable models