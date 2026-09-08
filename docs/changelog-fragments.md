# Changelog fragments

Drop ONE file per pull request into `changelog.d/` instead of editing
`CHANGELOG.md`:

    changelog.d/2291-notes-area.md

Name it `<pr-number>-<short-slug>.md` or, when the change is tracked by a
task card, `tsk-<cardid>-<short-slug>.md`. Write the bullet exactly as it should
appear in the changelog. The trailing `(#PR)` is NOT required: the fragment is
authored inside the commit that does the work, before the pull request number
exists. Nothing attaches the reference later either — the collator does not
inject one (see "At release time" below). Add it by hand if you want it and
you know the number; a `tsk-<cardid>` filename carries the card id instead,
which is the traceable link for fragments written by a lane.

    - Projects gain a Notes area: title + markdown notes per project.

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

Reruns are safe: if the `## [<version>]` section already exists (a previous run
died between writing the section and deleting the fragments), the collator does
not insert a duplicate. It consumes only leftover fragments whose content is
already present in `CHANGELOG.md`; a fragment that landed after the failed run
is folded nowhere, so it is kept on disk and the rerun exits non-zero naming
it — fold it by rerunning with the correct (next) target version.

The collator does not currently inject `(#PR)` references into the folded
output. If that is ever needed, the injection point would live in
`scripts/collate_changelog.py` alongside the bullet-grouping logic; that is a
separate card.
