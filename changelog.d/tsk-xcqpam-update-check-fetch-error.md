### Fixed

- The `/api/settings/update-check` response now surfaces the actual `git fetch` error text in `fetch_error` instead of always reporting "unknown error", because the fetch subprocess output is no longer discarded to DEVNULL.
