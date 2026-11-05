### Fixed

- `TaskReportRequest` now rejects whitespace-only `worker` and `resource_id` values (422), and `report_task` verifies the worker HMAC once instead of twice.
