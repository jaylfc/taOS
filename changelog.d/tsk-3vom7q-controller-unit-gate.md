### Fixed
- Controller unit gate: `test_controller_unit_entrypoint.py` now parses `[Service]` sections generically from every shipped unit source instead of relying on a hardcoded heredoc opener in `install.sh`, closing the fail-open defect where a one-space edit to the heredoc opener silently disarmed the gate while `-m uvicorn` shipped to production.
