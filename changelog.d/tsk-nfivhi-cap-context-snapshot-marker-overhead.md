### Fixed

- Added regression coverage for `_cap_context_snapshot()` on many-small-fields snapshots (long field names and short values, and many short-named fields) staying within the 32768-byte limit. The marker-overhead accounting fix this card targeted was independently landed with a more thorough byte-precise budget in tsk-kkxn6f's `_build_truncated_marker()`; this fold pass kept that implementation and this card's added tests.
