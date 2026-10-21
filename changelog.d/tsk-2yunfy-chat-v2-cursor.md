### Fixed
- `GET /api/chat/v2/channels/{id}/messages` no longer sends `before` to the bus as the `since` cursor (which returned NEWER messages when a client asked for older ones). On a bus-backed thread `before` now returns 400 with a hint to page forward; the new `since` param (a message ts) is passed straight to the bus. The local-store fallback keeps its `before` paging (tsk-2yunfy).
