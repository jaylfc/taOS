### Fixed

- Proxy ceiling guard now preserves the operator (`<`, `<=`, `==`) alongside the version in `_effective_upper_bound` and `_parse_snapshot`, so `<15` correctly fails against `<=15` and `==15`