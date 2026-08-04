# Changelog fragments

Drop ONE file per pull request into `changelog.d/` instead of editing
`CHANGELOG.md`:

    changelog.d/2291-notes-area.md

Name it `<pr-number>-<short-slug>.md` and write the bullet exactly as it should
appear in the changelog, including the trailing `(#PR)`:

    - Projects gain a Notes area: title + markdown notes per project (#2291).

Put `### Added`, `### Fixed`, `### Changed`, `### Removed` or `### Security` on
its own first line when the section matters; the collator groups by section and
defaults to `### Added`.

## Why files instead of editing CHANGELOG.md

Every PR that edits `CHANGELOG.md` writes at the same `[Unreleased]` anchor, so
any two concurrent PRs conflict there by construction, and each merge
re-conflicts every other open PR. With N PRs open that is O(N^2) rebases which
touch no real code. On 2026-08-04 that cost seven rebases in one session.
Distinct filenames cannot conflict.

Editing `CHANGELOG.md` directly still satisfies the doc gate, so nothing breaks
if you forget; the fragment is simply the path that does not cost anyone a
rebase.

## Why this file is not in changelog.d/

The doc gate accepts `changelog.d/*.md` as proof that a user-visible change was
documented. A `README.md` sitting in that directory would match the same glob,
so a PR could satisfy the changelog rule by touching the readme and shipping no
changelog at all. That is not hypothetical: it was caught by probing the gate
during the change that introduced fragments, where the gate went green on a
route change carrying no changelog entry. `changelog.d/` therefore contains
nothing but fragments and a `.gitkeep`.

## At release time

The version bump runs:

    python3 scripts/collate_changelog.py <version>

which folds every fragment into a new `## [<version>] - <date>` section beneath
`[Unreleased]`, groups them by section in Keep-a-Changelog order, and deletes the
fragments in the same commit. `--dry-run` prints the section without touching
anything.
