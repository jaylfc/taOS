### Fixed
- `install-hailo.sh`: upstream hailo-ollama.service units without the OLLAMA_HOST marker are now correctly refused (exit 3) instead of being mistaken for a taOS install. The detection now requires the marker to be present and match `OLLAMA_HOST=127.0.0.1:7836`.
- Replaced two fragile text-grep tests in `tests/test_install_hailo_preexisting_refusal.py` with the existing behavioral tests that properly assert caller script conflict handling.
- Added `test_upstream_unit_without_marker_is_refused` to prevent regression of the marker-absence bug.