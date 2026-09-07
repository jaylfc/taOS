### Fixed
- `LLMProxy.start()` in `tinyagentos/llm_proxy.py` now exports `TAOS_TRACE_URL`
  derived from the controller's bound port so the `TaosLiteLLMCallback` inside
  the LiteLLM subprocess can POST trace, lifecycle and spend events to the
  correct controller instead of silently dropping them on non-default ports
  (#tsk-j2l2qy).
- `TaosLiteLLMCallback` in `tinyagentos/litellm_callback.py` now falls back to
  `TAOS_PORT` (defaulting to 6969) when `TAOS_TRACE_URL` is unset, so standalone
  callers and custom-port installs still emit traces (#tsk-j2l2qy).
