# Design: Real Model Move Between Cluster Workers

## Overview

This design specifies how to move a model from a source worker to a target worker in the taOS cluster. The move is initiated by an admin via the controller, executed by the target worker pulling the model files from the source worker (or controller's store), verified for integrity, and only then the source is told to unload. Two prior attempts (#4395, #4407) simulated the move on the controller; this design requires worker-side support.

---

## 1. Where Models Live and How Workers Report Them

### On-disk layout (source of truth)

All backend installers write into a single unified tree:

```
~/models/<backend>/<family>/<manifest_id>/<filename>
```

Examples:
- `~/models/rk-llama.cpp/gemma/gemma-4-e2b-gguf/gemma-4-E2B-it-Q4_K_M.gguf`
- `~/models/llama-cpp/qwen3.5/qwen3.5-9b/qwen3.5-9b-Q4_K_M.gguf`

Reference: `tinyagentos/installers/model_paths.py:30-103` (`models_root()`, `backend_model_dir()`, `family_from_manifest()`).

Ollama is intentionally not migrated here; it manages its own blob store (`~/.ollama/models`).

### Worker heartbeat payload

The worker reports its live model catalog on every heartbeat (5s interval). The heartbeat is sent to `POST /api/cluster/heartbeat` (HMAC-signed, `worker_auth.py:33-97`).

Payload fields relevant to models (`worker/agent.py:1166-1194`):

```python
payload = {
    "name": self.name,
    "backends": backends,  # list of dicts, each with "models" and "loaded_models"
    "models": flat_models,  # derived from backends for backward compat
    "storage_cap_bytes": snap["storage_cap_bytes"],
    "storage_used_bytes": snap["storage_used_bytes"],
    "bytes_deduped_total": snap["bytes_deduped_total"],
    "free_vram_mb": vram["free_vram_mb"] if vram else None,
    "used_vram_mb": vram["used_vram_mb"] if vram else None,
    ...
}
```

Each backend entry (`worker/agent.py:558-572`) carries:
- `models`: all models the backend has on disk (from `/api/tags`, `/v1/models`, etc.)
- `loaded_models`: subset actually resident in memory (from `/api/ps` for Ollama/rkllama)

The controller aggregates this in `ClusterManager.heartbeat()` (`cluster/manager.py:605-627`), flattening `backends[].models` into `worker.models` and `backends[].available_models` into `worker.available_models`.

### How a worker loads a model today

| Backend | Load mechanism | Controller endpoint |
|---------|----------------|---------------------|
| Ollama | `POST /api/pull` to backend | `POST /api/models/pull` (`routes/models.py:686-740`) |
| rkllama | `RkllamaInstaller.install()` calls backend's `/api/pull` | `POST /api/models/download` (`routes/models.py:369-473`) |
| llama-cpp / vLLM / exo / MLX | File placed in `~/models/...`; backend loads on request | `POST /api/models/download` (`routes/models.py:496-521`) |
| sd-cpp | File placed in `~/models/...` | `POST /api/models/download` |

All downloads are tracked by `DownloadManager` (`download_manager.py:97-460`) with progress exposed via `GET /api/models/downloads/{id}` (`routes/models.py:534-541`).

---

## 2. The Pull: Target Worker Fetches Model from Source

### New controller → target worker command

The controller sends a signed command over the **existing authenticated worker channel** (HMAC worker auth, `worker_auth.py:33-97`). No new open port.

**New route on worker:** `POST /api/worker/model/fetch` (HMAC-gated, same auth as `/api/worker/deploy` and `/api/worker/remote` in `routes/cluster.py:1239-1314`).

**Request body:**
```json
{
  "model_id": "qwen3.5-9b",
  "source": "worker",  // or "controller" for archived/controller-local models
  "source_worker": "gpu-node-1",  // required if source="worker"
  "backend": "llama-cpp",
  "manifest_id": "qwen3.5-9b",
  "files": [
    {"relative_path": "llama-cpp/qwen3.5/qwen3.5-9b/qwen3.5-9b-Q4_K_M.gguf", "sha256": "abc123...", "size_bytes": 5368709120}
  ],
  "download_id": "model-move-qwen3.5-9b-gpu-node-2-abc123"
}
```

**Response:** `202 Accepted` with `{"status": "started", "download_id": "..."}`. Progress is tracked via the existing `DownloadManager` mechanism (see §5).

### How the target downloads

The target worker uses the **existing model file serving endpoint** on the source worker. The source worker must expose a new read-only endpoint:

**New route on source worker:** `GET /api/worker/model/file?path=<relative_path>` (HMAC-gated, same auth).

- The `path` is the relative path under `models_root()` (e.g., `llama-cpp/qwen3.5/qwen3.5-9b/qwen3.5-9b-Q4_K_M.gguf`).
- The source validates the path stays within `models_root()` (path traversal guard, `model_paths.py:186-193`, `download_installer.py:279-282`).
- The source streams the file with `Content-Length` and `Content-Type: application/octet-stream`.
- The target downloads using `download_file()` from `installers/download_installer.py:60-179` (which already handles SHA256 verification, resume via `.part` files, and progress callbacks).

**Controller coordination flow:**
1. Admin calls `POST /api/cluster/models/move` (admin-only, new route) with `{model_id, from_worker, to_worker}`.
2. Controller looks up the model in `from_worker`'s `available_models` to get backend, manifest_id, and file list with recorded SHA256.
3. Controller calls `POST /api/worker/model/fetch` on `to_worker` (HMAC-signed via `pairing.py:79-98`).
4. Target worker downloads each file from `from_worker` via `GET /api/worker/model/file`.
5. Target verifies each file against the SHA256 provided by the controller (recorded at download/registration time, §3).
6. On success, target loads the model into its backend and reports it in the next heartbeat.
7. Only then controller tells `from_worker` to unload (via `POST /api/worker/deploy` with a new `unload-model` command or backend-specific API).

---

## 3. Integrity: SHA256 Verification Against Recorded Hash

### Where the hash is recorded

At model download/registration time, the SHA256 is stored in the variant manifest (`variant.sha256`). The catalog manifests (YAML) carry this per variant.

- `DownloadManager._validate_download()` (`download_manager.py:221-258`) verifies the downloaded file against `expected_sha256` from the variant.
- `download_file()` (`installers/download_installer.py:176-178`) raises `ValueError` on mismatch and deletes the file.
- The controller's `DownloadManager` passes `variant.get("sha256")` to `start_download()` (`routes/models.py:509`).

### During move

The controller includes the recorded SHA256 for each file in the `POST /api/worker/model/fetch` payload. The target worker verifies **against that recorded hash**, not against a copy.

- The target's `download_file()` call receives `expected_sha256` from the payload.
- If verification fails, the `.part` file is deleted, the download task is marked `error`, and the controller is notified (via heartbeat `status` field or a dedicated callback).
- The source worker does **not** re-compute the hash at serve time; it simply streams the file. The trust anchor is the manifest-recorded hash.

---

## 4. Space Check: Target Verifies Free Space Before Downloading

The target worker checks its own free space **before** starting the download.

- The worker reports `storage_cap_bytes` and `storage_used_bytes` on every heartbeat (`worker/agent.py:1176-1178`, `cluster/worker_capacity.py`).
- The target calculates `free_bytes = storage_cap_bytes - storage_used_bytes`.
- Required space = sum of `size_bytes` for all files in the fetch request.
- If `free_bytes < required_bytes * 1.1` (10% headroom), the target refuses with a clear error:

```json
{
  "error": "insufficient_space",
  "message": "Need 5.9 GB but only 4.2 GB free on target worker",
  "required_bytes": 5900000000,
  "available_bytes": 4200000000
}
```

- The check happens in the `POST /api/worker/model/fetch` handler before spawning the download task.
- HTTP 507 (Insufficient Storage) with the above JSON body.

---

## 5. Progress: Reuse Existing Download/Progress Mechanism

The target worker uses the **existing `DownloadManager`** (`download_manager.py:97-460`) to track the fetch.

- The controller generates a `download_id` like `model-move-{model_id}-{target_worker}-{short_hash}`.
- The target calls `DownloadManager.start_download()` for each file (or a single multi-file task).
- Progress is exposed via the existing routes:
  - `GET /api/models/downloads` , list all downloads with progress
  - `GET /api/models/downloads/{download_id}` , per-download progress
- The UI and `taosctl` already poll these endpoints (`routes/models.py:524-541`, `download_manager.py:212-213`).
- **Admin-only status route:** `GET /api/cluster/model-move/{move_id}` on the controller (new route) aggregates per-file progress from the target worker's `DownloadManager` via a worker-side status endpoint `GET /api/worker/model/fetch/{download_id}/status`.

---

## 6. Switch: Target Loads, Reports, Then Source Unloads

### Sequence

1. **Target downloads and verifies** all files (as above).
2. **Target loads the model** into its backend:
   - For Ollama/rkllama: call backend's `/api/pull` with local file (or register the file in the backend's store).
   - For llama-cpp/vLLM/exo/MLX: files are already in the correct `~/models/...` layout; the backend will load on first request.
   - For sd-cpp: files in place; backend loads on request.
3. **Target reports the model in its next heartbeat** (within 5s). The heartbeat includes the model in `backends[].models` and, once loaded, in `backends[].loaded_models`.
4. **Controller confirms** the target reports the model as `loaded` (via `ClusterManager.heartbeat()` updating `worker.loaded_models`).
5. **Controller tells source to unload**:
   - New command `unload-model` in `DEPLOY_COMMANDS` (`routes/cluster.py:1203-1212`, `worker/deploy.py:24-38`).
   - Controller calls `POST /api/worker/deploy` on source with `{"command": "unload-model", "model_id": "..."}`.
   - Source worker's `taos-deploy-helper.sh` gets a new `cmd_unload_model()` that calls the backend's unload API (Ollama: `DELETE /api/delete`, llama-swap: `POST /unload`, etc.) and removes the model files from `~/models/...`.
6. **Failure handling**:
   - If target download fails: source keeps serving; target cleans `.part` files (already done by `DownloadManager._download()` on error, `download_manager.py:380-390`).
   - If target loads but heartbeat doesn't show it: controller retries for 30s, then aborts and cleans target's partial files.
   - If source unload fails: controller alerts admin; target is already serving.

---

## 7. Agent Pins Move Only After Target Reports Model Loaded

Agents are pinned to workers via `config.agents[i].remote` (`routes/cluster.py:764-771`).

- The controller **does not update** `agent.remote` until the target worker's heartbeat shows the model in `loaded_models`.
- Once confirmed, the controller updates the agent's `remote` field to the target worker and saves config (`config.save_config_locked()`).
- If the move is cancelled or fails, the agent pin stays on the source worker.

---

## 8. Security

### Who may start a move

- **Session admin only** (same as other fleet mutations in `routes/cluster.py:36-42`, `_ADMIN = [Depends(require_admin)]`).
- New route: `POST /api/cluster/models/move` with `_ADMIN` dependency.

### What a compromised worker could do with the command

The `POST /api/worker/model/fetch` command is HMAC-signed by the controller using the target worker's signing key (`pairing.py:79-98`). A compromised **source** worker could:
- Serve corrupted files → caught by SHA256 verification on target.
- Serve files outside `models_root()` → blocked by path traversal guard (`model_paths.py:186-193`).
- Refuse to serve → move fails, source keeps serving.

A compromised **target** worker could:
- Claim to have downloaded but not actually have the files → caught by heartbeat `loaded_models` check.
- Request arbitrary files from source → source validates `path` is under `models_root()`.

A compromised **controller** (admin session) could:
- Initiate moves arbitrarily → admin privilege assumed.

### Size limits

- Max total model size per move: 100 GB (configurable via `TAOS_MAX_MODEL_MOVE_GB`).
- Per-file limit: 50 GB.
- Enforced in `POST /api/cluster/models/move` before dispatching to target.

### Path validation

- Source worker validates `path` in `GET /api/worker/model/file` stays within `models_root()` using `resolve().relative_to()` guard (`model_paths.py:186-193`, `model_archive.py:186-193`).
- Target worker writes to `backend_model_dir()` which also validates (`model_paths.py:85-103`).

---

## 9. Implementation Cards

### Card 1: Worker-side support (target + source)
**Files:** `worker/agent.py` (new HTTP server routes), `worker/deploy.py` (new `unload-model` command), `scripts/taos-deploy-helper.sh` (new `cmd_unload_model()`), `installers/download_installer.py` (reuse `download_file()` for worker-to-worker transfer).

**Tests:**
- Unit: `GET /api/worker/model/file` serves file, rejects traversal, returns 404 for missing.
- Unit: `POST /api/worker/model/fetch` downloads from source, verifies SHA256, reports progress via DownloadManager.
- Unit: `unload-model` command calls backend unload API and removes files.
- Integration: Two workers in test cluster; move a model from worker A to B; verify B loads it, A unloads, agent pin updates.

### Card 2: Controller orchestration
**Files:** `routes/cluster.py` (new `POST /api/cluster/models/move`, `GET /api/cluster/model-move/{move_id}`), `cluster/manager.py` (track move state), `cluster/model_resolver.py` (reuse `find_model_hosts` to locate source).

**Tests:**
- Unit: Move request validates admin, finds source via model_resolver, dispatches fetch to target.
- Unit: Move state machine: pending → fetching → verifying → loading → switching → unloading → complete.
- Unit: Failure at each step leaves source serving, cleans target partials.
- Integration: Admin calls move API; verify controller coordinates workers correctly.

### Card 3: UI / taosctl
**Files:** `desktop/src/` (Cluster page: move button, progress modal), `cli/taosctl/commands/` (new `taosctl model move` command).

**Tests:**
- Unit: CLI command calls controller API, polls status, prints progress.
- E2E: Move a model via UI; verify progress bar updates; verify agent pin moves after load.

### Card 4: Tests (cluster-level)
**Files:** `tests/cluster/test_model_move.py` (new).

**Tests:**
- Happy path: model moves from worker A to B, agent pin updates, both workers healthy.
- Insufficient space on target: move refused, source unchanged.
- SHA256 mismatch: move fails, source unchanged, target cleans up.
- Source offline during move: move fails, target cleans up, admin notified.
- Concurrent moves: two moves for different models proceed independently; same model serialized.
- Network partition during download: resume via `.part` file works.

---

## Appendix: Existing Code References

| Feature | File:Line |
|---------|-----------|
| Worker heartbeat payload | `worker/agent.py:1166-1194` |
| Model on-disk layout | `installers/model_paths.py:30-103` |
| Backend model detection | `worker/agent.py:740-769` (`_probe_models`) |
| Loaded models detection | `worker/agent.py:707-738` (`_probe_loaded_models`) |
| HMAC worker auth | `cluster/worker_auth.py:33-97` |
| Worker request signing | `worker/pairing.py:79-98` |
| DownloadManager progress | `download_manager.py:85-95, 212-213` |
| Download progress routes | `routes/models.py:524-541` |
| SHA256 verification | `download_manager.py:221-258`, `installers/download_installer.py:176-178` |
| Path traversal guard | `model_paths.py:186-193`, `model_archive.py:186-193` |
| Worker deploy endpoint | `routes/cluster.py:1239-1279`, `worker/deploy.py:24-38` |
| Worker storage reporting | `worker/agent.py:1176-1178`, `cluster/worker_capacity.py` |
| Cluster model resolver | `cluster/model_resolver.py:113-206` (`find_model_hosts`) |
| Agent pinning | `routes/cluster.py:764-771` |
| Admin auth dependency | `routes/cluster.py:36-42` |
| Model download (HTTP) | `installers/download_installer.py:60-179` |
| taos-deploy-helper commands | `scripts/taos-deploy-helper.sh:24-38, 734-756` |

---

## Docs-Reviewed

This design doc is the sole deliverable for this task. No code changes are included. Implementation cards will be filed separately after lead review.