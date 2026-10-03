### Fixed
- updater preflight now accepts an explicit tracked branch instead of resolving it internally from a non-existent config store, preventing false branch_not_on_origin errors on local-only checkouts.
- update-check and update routes now pass the user's saved tracked channel to preflight before fetching.
- git fetch error decoding uses errors="replace" to avoid 500s on non-UTF-8 output.
