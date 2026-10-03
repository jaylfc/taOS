## Device API v1

The full spec now lives in `docs/device-api-v1.md`; routes under `/api/devices`
are bearer-authenticated. The following routes are public (no bearer token):

- `POST /api/devices/pair-requests`
- `GET /api/devices/pair-requests/{pair_request_id}`
