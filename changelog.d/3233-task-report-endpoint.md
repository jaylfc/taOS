### Added

- Cluster: `POST /api/cluster/tasks/report` lets external schedulers report GPU job outcomes (`worker`, `resource_id`, `lease_id`, `caller`, `model`, `duration_s`, `status`, optional `tokens`). Authenticated like the lease endpoints (admin session, local token, or worker HMAC). Outcomes are stored in the trace store as `task_report` events under the `taos-cluster-tasks` slug so the Observatory and benchmark layers can see external GPU work (#3233).
