### Fixed

- `bot-review-gate` now treats fork PRs as requiring lead review: a fork PR stays red until a maintainer approves or the `lead-reviewed` label is applied. `scripts/check_bot_review.py` exits `EXIT_FORK_UNREVIEWED` (3) when neither condition is met. `bot-review-allow` does not waive this verdict.
