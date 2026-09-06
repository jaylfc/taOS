### Fixed
- Notification archive tab now merges store-derived rows with fetched server-only rows instead of replacing, so server-only archived rows survive store mutations and clearAll.
- In-flight archive fetch spinner no longer drops early on rapid tab toggles when an aborted request's finally fires after a newer request has started.