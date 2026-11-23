### Fixed

- Deploy preflight now accepts covering root subuid/subgid ranges (not only the exact `root:<uid>:1` line) and deploys fail fast with a clear error when no covering range is present.
