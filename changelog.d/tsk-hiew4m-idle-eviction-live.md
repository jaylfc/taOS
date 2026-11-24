### Added

- Idle TTL eviction now runs live through the gateway: the model scheduler's `evict_fn` calls `backend_unload.unload_model` for unload-capable backends (llama-swap on dev), retains residents when unload fails for retry on next sweep, and logs but does not evict for backends without unload mechanisms (ollama, rkllama on dev). The in-use guard from tsk-mo6f5o prevents eviction of models with active requests. Gateway registration sites now pass `model_class` derived from model name and size for proper TTL defaults.
