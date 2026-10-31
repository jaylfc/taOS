### Added

- An MLX (Apple Silicon) backend installer. Installing a model on the `mlx`
  backend now goes through `MLXInstaller` instead of the generic download
  fallback: it verifies the host is Apple Silicon, provisions the `mlx-lm`
  runtime, and pulls the model (`mlx_repo`, falling back to `hf_repo`, or a
  single `download_url`) into the shared models tree
  (`<models root>/mlx/<family>/<manifest_id>/`, i.e. `TAOS_MODELS_ROOT` or the
  install root's `models/`). On any other platform it fails with a clear message
  rather than dropping a file the runtime can never load.
- Apple Silicon hosts register their GPU as the `gpu-metal` scheduler resource
  (`docs/design/resource-scheduler.md`) instead of the CUDA-indexed
  `gpu-cuda-0`, in controller discovery and in the worker's advertised
  inventory alike, so lease ids and resource names match the hardware. The A2A
  GPU lease default follows the host too: a claim that names no resource no
  longer names one a Mac worker cannot have. Port 7837 is reserved for the MLX
  server alongside 7833-7836.

### Changed

- The MLX runtime is installed into a venv of its own
  (`<install root>/apps/mlx-runtime/venv`, override `TAOS_MLX_VENV`) from a
  vendored, hash-pinned lock (`mlx_lm_requirements_py311|py312|py313.txt`,
  regenerate with `scripts/gen-mlx-lock.py`) with
  `pip install --require-hashes --only-binary=:all:`, instead of an unpinned
  `pip install mlx-lm` into the controller's interpreter. `mlx-lm` and `mlx`
  are pinned (`MLX_LM_VERSION` / `MLX_VERSION`) and neither the controller's
  packages nor an index can change what the runtime runs.
- `METAL_RESOURCE_NAME` and `metal_available()` moved from
  `installers/mlx_installer.py` to `hardware.py`, so the worker and the
  scheduler no longer import the installers package for a hardware fact.
  `metal_available()` now probes the device (`system_profiler`'s Metal Support
  line, memoised) instead of assuming every arm64 macOS host has a Metal GPU:
  an arm64 VM with no Metal device no longer registers `gpu-metal`, or any
  other GPU resource.
- The MLX installer reports no endpoint. `mlx_lm.server` serves one model per
  process, so the managed service belongs to the backend-activation slice, not
  to a model install; `install()` previously advertised
  `http://127.0.0.1:7837` that nothing served. That port stays reserved and
  `mlx_server_is_running()` is the health probe the service slice will use.
