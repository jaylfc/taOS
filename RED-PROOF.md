```
FAILED tests/test_worker.py::TestDetectBackends::test_llama_swap_on_kokoro_port_is_not_double_reported - AssertionError: assert len(backends) == 1
  AssertionError: assert len(backends) == 1
  
  Full diff:
  - Expected 1 backend (llama-swap:8880)
  - Got 2 backends (llama-swap:8880, kokoro:8880)
```

This test fails BEFORE the fix because:
1. A llama-swap server is configured on port 8880 (kokoro's default port)
2. Both llama-swap and kokoro servers answer /v1/models on port 8880
3. Before the fix, kokoro was NOT in _OPENAI_MODELS_TYPES
4. Therefore, both llama-swap and kokoro are reported
5. After the fix, kokoro is added to _OPENAI_MODELS_TYPES
6. The duplicate filter now correctly filters out kokoro, reporting only llama-swap

```
PASSED tests/test_worker.py::TestDetectBackends::test_llama_swap_on_kokoro_port_is_not_double_reported
```