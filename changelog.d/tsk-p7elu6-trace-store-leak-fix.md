### Fixed

Fixed trace_store.list() connection leak by opening read-only connections for listing and closing them after use instead of caching them. This prevents opening a connection and thread per bucket for every bucket touched during list operations.
