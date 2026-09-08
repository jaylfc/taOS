### Fixed

- `.github/scripts/check_all_skip.py` now counts pytest `errors` as a distinct outcome, prints the last 40 lines of pytest output when setup/teardown errors occur, and reports `N setup/teardown errors` instead of mislabeling the file as a collection failure. A file with only setup errors is no longer reported as `collection yielded 0 of N`.
