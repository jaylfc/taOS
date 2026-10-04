### Fixed
- Installer now uses `constraints.txt` (generated from `uv.lock`) to pin every `pip install` so the installed dependency set matches the licence-audited set exactly. A drift gate in `tests/test_install_licences.py` fails if `constraints.txt` diverges from `uv.lock`.
