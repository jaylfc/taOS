### Fixed

- **hub/posts.py `_append_locks`**: changed from `dict[int, asyncio.Lock]` keyed by `id(store)` to `weakref.WeakKeyDictionary` keyed by the store object itself, so GC of a HubStore reclaims its lock and prevents RuntimeError when a new store reuses the GC'd store's id().