### Fixed

- Calendar: date keys (`YYYY-MM-DD`) are now parsed as local dates via `parseDateKey` instead of UTC, fixing off-by-one day bugs in negative-offset timezones (e.g. America/Los_Angeles). Restored nine tests from dev covering header, weekdays, day count, prev/next, year wrap, today highlight, and Today button. Day cells now use `aria-pressed` and each cell's `aria-label` reflects its own date.
- Cluster: fixed invalid Tailwind variant `[@min-width:600px]:` → `min-[600px]:` so desktop header lays out horizontally; `text-xs` retained on tab buttons.
- Models: request counter ensures only the latest fetch clears loading; error banner with Retry now shows even when worker/cloud models are available (previously hidden). Added tests for stale-mount race and fallback-with-error.
- CreateProjectDialog: accounts for `visualViewport.offsetTop` when keyboard shrinks viewport so dialog stays anchored to visible top.
- Deleted junk changelog fragment `tsk-e2j6cm-review-blocker.md`.
