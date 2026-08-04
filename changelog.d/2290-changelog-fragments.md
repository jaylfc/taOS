### Changed

- Contributors add a `changelog.d/<pr>-<slug>.md` fragment instead of editing
  `CHANGELOG.md`, so concurrent PRs no longer conflict on the shared
  `[Unreleased]` anchor; `scripts/collate_changelog.py` folds fragments into a
  release section at bump time. Editing `CHANGELOG.md` directly still works.
