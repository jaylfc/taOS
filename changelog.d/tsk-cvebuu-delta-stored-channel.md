### Fixed

- `POST /api/chat/messages/{id}/delta` broadcasts on the message's stored `channel_id` when a bound agent omits `channel_id` in the request body, ensuring delta tokens reach the correct channel subscribers even without explicit channel identification.
