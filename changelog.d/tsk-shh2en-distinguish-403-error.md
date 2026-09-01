### Fixed

- reconcile now distinguishes HTTP 403 Forbidden from other infrastructure errors when PATCHing stale bot-review-gate check runs; a distinct "override could not be applied" message is printed on 403 rather than the generic "stale FAILURE survived reconcile" line, making permission faults visible and distinguishable from genuinely unwaived PRs