### Added
- GET /api/library/jobs returns the cross-item ingest job list, honouring ?state= and ?limit=.
- POST /api/library/jobs/{id}/retry re-queues a job in error state and returns 404 for unknown ids or 409 for non-error jobs.
