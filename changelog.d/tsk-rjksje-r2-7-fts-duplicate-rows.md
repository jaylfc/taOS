### Fixed

- Knowledge search (R2-7): `update_item` now deletes the old FTS5 row before
  re-inserting, instead of `INSERT OR REPLACE` which appends a duplicate row
  on a standalone FTS5 table, so re-indexing an item no longer leaves stale
  text searchable.
