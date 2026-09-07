### Fixed
- `POST /api/cluster/workers/update-all` now returns 202 with a job id immediately and runs the rolling update as a tracked background task. Poll `GET /api/cluster/workers/update-all/<job_id>` for progress instead of blocking the HTTP request for up to n x 300 s (R2-24).
