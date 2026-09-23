### Fixed
- LiteLLM proxy startup no longer blocks the event loop when `lsof` is slow or hung on the restart-over-stale-LiteLLM path; `_pids_listening_on` now runs in a thread with a 5s timeout and the health endpoint stays responsive.
