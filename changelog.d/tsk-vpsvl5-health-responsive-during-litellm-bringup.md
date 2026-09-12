### Fixed

Fixed LiteLLM startup blocking by running `migrate()` in a thread to prevent event-loop stalls. The `litellm_migrate.migrate()` function now runs in `asyncio.to_thread()` from the `_litellm_bringup()` background task, ensuring the health endpoint remains responsive during startup (addresses the 38.5s stall on Pi 4). This matches the pattern used in `llm_proxy.write_config()`.
