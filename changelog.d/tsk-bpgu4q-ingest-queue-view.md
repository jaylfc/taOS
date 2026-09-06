### Added

- Library app ingest queue view: a pane listing active and failed pipeline jobs with stage, error text, and a per-job retry action. Polls the jobs endpoint every 3s while jobs are active and stops once the queue is idle (retry POST mocked until #2058).
