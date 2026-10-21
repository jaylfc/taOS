### Fixed

- Epoch-second `last_message_at` timestamps now sort correctly and display relative time labels properly by adding a `toMs()` helper that normalizes numbers (seconds/ms), numeric strings, and ISO strings to milliseconds before use in `sortKey()` and `formatRelativeTime()`.
