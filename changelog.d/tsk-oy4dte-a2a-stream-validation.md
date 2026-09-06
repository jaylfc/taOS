### Fixed

- The A2A bus SSE stream proxy (`/api/a2a/bus/stream`) now rejects non-finite `since` cursor values (nan, inf, -inf) with a 400 error instead of forwarding them to the bus, matching the validation already present on the sibling messages endpoint. It also rejects unknown query parameters with a 400 instead of silently ignoring them, preventing the same incremental-read confusion that was fixed on `/api/a2a/bus/messages`.
