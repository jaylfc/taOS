### Fixed

- Chat search now respects the `limit` parameter on readable channels only. Previously, messages from unreadable channels consumed the LIMIT before filtering, causing fewer (or zero) readable results to be returned. The fix computes readable channel IDs first and passes them to the search query, so the LIMIT applies only to channels the caller can read.