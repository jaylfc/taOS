### Fixed
- `llm_proxy.py` readiness poll now checks `proc.poll()` each iteration and fails fast with the stderr tail when the proxy process exits at startup, instead of blocking for the full 120 s timeout (R2-29).
