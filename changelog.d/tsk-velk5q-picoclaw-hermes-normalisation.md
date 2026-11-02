### Fixed

- taOS Agent `apply_framework`: normalise the configured framework value before comparing it to `hermes`, so `Hermes` or ` hermes` selects hermes consistently with `decide_framework` instead of skipping the hermes probes. Also stop expecting `hermes` to be rejected as an unknown framework value in `test_unknown_values_are_rejected_and_change_nothing`.
