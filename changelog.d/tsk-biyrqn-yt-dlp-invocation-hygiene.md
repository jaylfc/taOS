### Fixed

- YouTube and X fetchers now resolve `yt-dlp` via `shutil.which` and raise a named error when it is not installed. Subprocess calls use `--dump-single-json` with `decode(errors="replace")`, track processes per request with cleanup on exit, enforce a per-subprocess timeout via `asyncio.wait_for`, and combine thumbnail and caption download into a single invocation. Caption candidates are sorted deterministically, and thumbnail discovery no longer assumes a `.png` extension.
