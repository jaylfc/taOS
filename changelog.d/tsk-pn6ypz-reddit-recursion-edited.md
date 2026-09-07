### Fixed

- Reddit comment fetcher (`knowledge_fetchers/reddit.py`) now walks comment
  trees iteratively with depth (2 000) and count (10 000) caps, eliminating
  `RecursionError` on pathologically deep trees. The `edited` field is also
  corrected: `edited=True` (Reddit's legacy boolean) is mapped to `None`
  instead of being coerced to `1.0` (the 1970 epoch), while genuine numeric
  timestamps and `False` are preserved.
