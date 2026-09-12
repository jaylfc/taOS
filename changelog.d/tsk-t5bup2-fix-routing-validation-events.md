### Fixed

- Projects router: Changed `require_owner_or_admin` to `_get_owned_project` for 6 routes to provide consistent 404 behavior for non-owners
- Projects router: Updated `delete_element` mode parameter to use `Literal["strict", "untag"]` for type safety
- Projects router: Consolidated `_SLUG_RE` regex definition from 3 locations to 1 in `element_store.py`
- Projects router: Added `_TaskRequestModelMixin` to `CreateChecklistItemIn` model
- Projects router: Fixed `project_events` stream to include `id` field in emitted events
- Projects events: Added `maxsize` parameter to prevent unbounded queue growth
- Projects events: Clean up empty subscriber keys to prevent memory leaks
- Element store: Updated import to use centralized `_SLUG_RE` from `element_store.py`
- Fixed imports in projects.py: removed unused `re` import, added `Literal` and `_SLUG_RE` imports