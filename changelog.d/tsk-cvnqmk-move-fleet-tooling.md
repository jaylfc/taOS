### Fixed
- Moved lane-runnable agent tooling (`red_first.sh`, `prove_red_first.sh`, `resume_arm_time.py`, `test_resume_arm_time.py`, `liveness_check.py`, `orphan_check.sh`, `verify_routes.py`) from `~/.taos-team` and `~/.taos-website-agent` into `tools/` in jaylfc/taOS, so lanes can review and change them in a normal PR. Shims left at the old paths redirect to the canonical copies.
