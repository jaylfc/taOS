### Fixed

Device bearers can now write to share destinations with per-destination authorization. The three ingest endpoints (library ingest, project files upload, and chat messages) are now accessible to device bearers with appropriate authorization checks:

- `POST /api/library/ingest` → into that user's library only
- `POST /api/projects/{slug}/files/upload` → the user must have WRITE access to that project  
- `POST /api/chat/messages` → the user must be a MEMBER of `channel_id`; the author is the device's user and must not be settable from the request body

This extends the device-bearer self-service routes to cover all share destinations, enabling paired phones to post shared items to each returned destination as intended by the original design.