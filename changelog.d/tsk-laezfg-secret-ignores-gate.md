### Security

- **CI**: `secret-ignores-gate` workflow and `scripts/check_secret_ignores.py` now
  assert, on push to `master`/`dev`/`release/*` and on PRs to those branches, that the
  committed `.gitignore` still contains every secret-protection rule (`*.key`,
  `*.p8`, `identity.json`, `*credentials.json`, `*creds*.json`, the `*_private.*` key
  shapes, `secrets/`, `data/hub/`, and more) and that known secret-shaped paths
  (`data/hub/identity.json`, `foo.key`, `creds.json`, `x.p8`, `y_credentials.json`,
  ...) are all reported ignored by `git check-ignore`. Closes the "promotion must be
  verified, not assumed" gap from #2171/#2173: a `.gitignore` conflict resolution can
  quietly drop a key-material rule while every test stays green. Removing any one
  pattern is proven to fail the gate by a parametrized test.
