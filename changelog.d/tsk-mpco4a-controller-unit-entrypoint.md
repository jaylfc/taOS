### Fixed
- Controller units now start via `python -m tinyagentos` instead of invoking uvicorn directly, so the bounded graceful-shutdown handler in `__main__.py` runs on every restart and SIGKILL no longer occurs on low-end ARM hardware.
