### Fixed

- **bot-review-gate false-red on clean CodeRabbit reviews**: `scripts/check_bot_review.py` now recognises a completed CodeRabbit review that found zero findings. An auto-summary comment carrying both the `No actionable comments were generated in the recent review` marker and the quota-consumed `Included review availability:` line is classified as a real review outcome and exits 0 with the Run ID printed, instead of being merged with scaffolding stubs and forcing a hand-applied `bot-review-allow` on every clean PR.
