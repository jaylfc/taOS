### Fixed

- `bus_stream` now parses `since` from query params manually instead of relying on FastAPI's `float | None = None` annotation, returning project-consistent 400 errors for non-numeric and non-finite values (matching `/api/a2a/bus/messages` behavior). Docstring updated to document `since` as a message `ts` (float), NOT an id, and that unknown query params are rejected 400.