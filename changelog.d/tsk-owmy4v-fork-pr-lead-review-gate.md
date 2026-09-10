### Fixed
- Fork PRs now require lead review to pass the `bot-review-gate`: the gate detects fork PRs and stays red with `EXIT_FORK_UNREVIEWED` (3) until a maintainer approves or applies the `lead-reviewed` label. The `bot-review-allow` label no longer waives fork PR verdicts.
