### Fixed

- `/api/openclaw/bootstrap` now re-mints a missing `llm_key` for a deployed agent instead of returning 409 forever, and the agents model/permitted-models routes re-mint before giving up on a missing key.