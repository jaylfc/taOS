### Fixed
- Grant expiry is never silently dropped or lengthened on additive paths (scope-request approve, consent handle-reuse). `add_grant` now keeps `min(existing, new)` unless `renew=True` is passed. The consent and scope-request approve bodies accept `renew: bool = False` to allow explicit renewal.
