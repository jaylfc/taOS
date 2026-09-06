### Fixed: workers on a beta can finally see a newer beta

- The worker update check compared versions by throwing away everything after
  the first `-`, so every beta of a release collapsed onto the same numeric
  tuple. taOS ships `1.0.0-beta.N`, so no beta ever saw a newer beta and no
  worker could cross from a beta to the GA of the same release; a version pin
  inherited the same blindness and accepted `1.0.0-beta.99` under a
  `1.0.0-beta.40` pin. Version strings are now parsed with `packaging`
  (PEP 440), so pre-releases order against each other and below their GA.
- The update channel of a version is now read from its parsed pre-release
  segments instead of by searching the raw string, so a GA release carrying
  build metadata such as `1.0.0+devbuild` is no longer classified as a dev
  build and withheld from stable- and beta-channel users.
- The optional-app catalog no longer reports a permanent "update available"
  for an app whose recorded version cannot be parsed: it used to fall back to
  `(0, 0, 0)`, which read as older than everything. An unrecognisable recorded
  version now means "no update", and a recorded pre-release correctly reads as
  older than the GA of the same release.
- `packaging` is now a declared runtime dependency; it was previously reachable
  only through the `proxy` extra and the dev group, so a bare `uv sync` did not
  install it.
