### Added

- **Backend unload: Ollama and RKLLama adapters (#4462)** — `backend_unload` now registers unloaders for `ollama` (`POST /api/generate` with `keep_alive: 0` and no prompt, the immediate-unload idiom) and `rkllama` (`POST /unload_model` on the deployed fork), alongside the existing llama-swap adapter, so the GPU work queue eviction path can free idle models on those backends too. A backend type with no unloader now logs that it is not unload-capable instead of returning False silently, so "cannot unload" is distinguishable from a failed unload.
