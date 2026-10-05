### Fixed

- Trimmed the GET `/api/device/v1/agents/{name}/avatar` route doc to keep the compiled routes doc under the 21200-char budget.
- Offloaded server-side LVGL 9 RGB565A8 avatar conversion off the event loop in `tinyagentos/routes/device_avatar.py` using `asyncio.to_thread`, preventing cache-miss responses from blocking the async loop.
