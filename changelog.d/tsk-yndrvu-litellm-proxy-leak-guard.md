### Fixed

- Add a session-level guard in `tests/conftest.py` that tracks `subprocess.Popen` calls spawning LiteLLM processes and fails the session if any survive past teardown.
- Fix `test_health_endpoint_responsive_during_litellm_bringup` to mock the LiteLLM subprocess and use free ports (17999 / 17998) instead of real defaults (7834 / 6969).
- Fix `test_readiness_fails_fast_when_proxy_crashes_at_startup` to call `LLMProxy.stop()` in a `finally` block and use a free controller port (14022).
