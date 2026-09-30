### Fixed

- Fork PRs now require lead review as the gate for this repository. Previously, fork PRs could bypass the bot-review-gate, but now they stay red until a maintainer applies `lead-reviewed` label or approves with admin/write permission. This prevents fork PRs from merging on vacuous green.

- Added `EXIT_FORK_UNREVIEWED` (exit code 3) to distinguish fork PR failures from other gate failures.

- The `bot-review-allow` label no longer waives fork PR verdicts: it waives stub-shaped bot output only, and fork PRs have no bot output to be stubbed.

- Added "Fork PRs" subsection to CONTRIBUTING.md explaining the lead review requirement and expected turnaround time.