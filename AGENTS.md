# AGENTS.md

Harness-agnostic entry point: read by any agent harness that loads `AGENTS.md`
(kilo, opencode, Claude, Cursor, ...).

## Changelog fragments

A non-test change under `tinyagentos/` or `desktop/src/` requires a
`changelog.d/<pr>-<slug>.md` fragment (or a `CHANGELOG.md` line) in the same
PR. The single-source rule lives in [`docs/changelog-fragments.md`](docs/changelog-fragments.md)
and is summarized in [`CONTRIBUTING.md`](CONTRIBUTING.md) (the "Changelog" section).
