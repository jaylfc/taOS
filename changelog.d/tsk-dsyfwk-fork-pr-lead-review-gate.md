### Fixed

- Fork PRs now require lead review as the gate for this repository. Previously, fork PRs could bypass the bot-review-gate, but now they stay red until a maintainer applies `lead-reviewed` label or approves with admin/write permission. This prevents fork PRs from merging on vacuous green.

- Added `EXIT_FORK_UNREVIEWED` (exit code 3) to distinguish fork PR failures from other gate failures.

- The `bot-review-allow` label no longer waives fork PR verdicts: it waives stub-shaped bot output only, and fork PRs have no bot output to be stubbed.

- An approval on a fork PR now must be on the current head sha: an older APPROVED review or a later CHANGES_REQUESTED by the same reviewer no longer passes the gate.

- Label fetch failures for fork PRs now fail closed (EXIT_ERROR) instead of falling through to the reviews check.

- Added "Fork PRs" subsection to CONTRIBUTING.md explaining the lead review requirement, the head-sha constraint, and the fail-closed permission read.

### Fixed

- Restored correct `hailo_model_zoo_genai.git` header in `scripts/install-hailo.sh`, undoing a stray revert that had changed it back to `hailo-ollama.git`.