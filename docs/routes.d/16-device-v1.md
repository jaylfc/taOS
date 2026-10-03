## Device API v1

The full spec now lives in `docs/device-api-v1.md`; routes under `/api/devices`
are bearer-authenticated **except**:

- `POST /api/devices/pair-requests`
- `GET /api/devices/pair-requests/{pair_request_id}`
