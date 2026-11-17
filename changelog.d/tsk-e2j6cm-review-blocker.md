### Fixed

- Calendar: `formatDateKey` now emits zero-padded `YYYY-MM-DD` (e.g. `2025-06-15`) instead of `2025-5-15`, so `new Date(selectedKey)` round-trips the clicked day and the day-panel heading shows the correct month/date. Verified by tapping an other-month cell and asserting the panel heading.