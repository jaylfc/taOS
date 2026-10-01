### Fixed

- Changed the `dompurify` npm override in `desktop/package.json` from a literal range (`^3.4.0`) to `$dompurify`, so npm no longer rejects it as conflicting with the direct dependency when Dependabot bumps the `spa-deps` group.
