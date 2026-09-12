### Fixed
- restored attribution values in doc_review first-write so reviewed_by, reviewed_at, changes_requested_by, and changes_requested_at are persisted on the initial approved or changes_requested transition
- removed the destructive rollback from BaseStore._insert_with_retry so id-collision retries no longer discard enclosing transaction writes
- collapsed the two _insert_with_retry definitions into one no-rollback copy on BaseStore; ProjectsDBStore-derived stores inherit it and no longer shadow it with a different body
