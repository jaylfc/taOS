### Fixed
- POST /api/trace now rejects kind="task_report" (kind 'task_report' is written by taOS only) to prevent forged task reports
- report_task now returns 403 for unidentified callers (fail-closed binding)
- Task reports store lease_id hash as lease_ref instead of raw lease_id in trace
- TaskReportRequest docstring now accurately describes rows being written under slug taos-cluster-tasks and readable via GET /api/trace