### Fixed

- Added deterministic timestamp-skew guards to notification tests, parametrising over `[same_second, pre_existing_1s_earlier]` to ensure tests pass even when rows have same-second timestamps. Tests now assert exactly one row matches the target title instead of using positional selection (`items[-1]`).

