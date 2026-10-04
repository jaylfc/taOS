# Release process

taOS uses semver beta: `1.0.0-beta.N`, incremented on every dev->master promotion.

## Steps

### 1. Bump version

Update the version string to the next `1.0.0-beta.N` in exactly these files (keep them identical):

- `pyproject.toml` line `version = "..."`
- `desktop/package.json` line `"version": "..."`
- `desktop/package-lock.json` -- BOTH root `version` fields: the top-level `version` AND `packages[""].version`. Do NOT regenerate the lockfile; only edit these two fields.
- `tinyagentos/__init__.py` line `__version__ = "..."`
- `uv.lock` -- the `tinyagentos` package entry's `version = "..."` (uv normalises `1.0.0-beta.N` to `1.0.0bN`). `test_version_lock_sync.py` fails the build if this drifts from `pyproject.toml`.

### 2. Update CHANGELOG.md

Move the items under `## [Unreleased]` into a new dated section at the top:

```
## [1.0.0-beta.N] - YYYY-MM-DD
```

Group bullets under `Added`, `Changed`, and `Fixed`. Keep each bullet one concise line.
Leave `## [Unreleased]` empty and ready for the next cycle.

### 3. Open a PR to dev

Commit the version bump and changelog update together. Open a PR targeting `dev`.
CI runs the backend pytest suite and frontend vitest on every PR; both must be green before merging.

### 4. Promote dev to master

Once the PR is merged to `dev`, open a follow-up PR from `dev` to `master`.
After that PR merges, the install-count telemetry at taos.my starts recording the new version for every fresh install.

**If the dev→master PR reports `BEHIND`** (master protection requires branches
up to date, and Dependabot merges land directly on master between releases),
a direct promotion cannot merge. Use the sync-branch pattern (beta.45/46/48
precedent):

1. Branch from `dev` (e.g. `sync/dev-to-master-beta.N`), merge `master` into
   it — the conflicts, if any, are lockfile-shaped (`uv.lock`,
   `desktop/package-lock.json`). Re-verify the version lines survived the
   merge (`test_version_lock_sync.py`).
2. PR that branch → `master`, CI green, merge.
3. **Back-merge master into dev** (PR `master` → `dev`) and confirm tree
   identity: `git diff origin/dev origin/master` must be EMPTY after it
   merges. Master-only content is an invisible surface — a promotion is not
   done until that diff is empty.

The `secret-ignores-gate` runs on the `master` push (and on the PR merge result)
and confirms the promoted `.gitignore` still ignores every secret-shaped path it
did on `dev` -- `identity.json`, `*.key`, `*.p8`, `*credentials.json`, `*creds*.json`
and the `*_private.*` key shapes, plus the `secrets/` and `data/hub/` rules. Re-run
it by hand if a conflict resolution touched `.gitignore`:

```bash
python3 scripts/check_secret_ignores.py
```

Do not skip this: a `.gitignore` conflict resolution can quietly drop a
key-material rule while every test stays green. The gate is the verification, not
an assumption.

### 5. Tag (the tag push publishes the GitHub Release)

On `master`, after the merge commit:

```bash
git tag -a v1.0.0-beta.N -m "v1.0.0-beta.N"
git push origin v1.0.0-beta.N
```

Pushing the tag runs `.github/workflows/release.yml`, which:

1. refuses to publish if the tagged commit's `pyproject.toml` version is not the tag,
2. takes the release body from the `## [1.0.0-beta.N]` section of `CHANGELOG.md`
   (`scripts/changelog_section.py`; it fails rather than publish empty or wrong notes),
3. creates the release as a draft, attaches the prebuilt desktop bundle, then publishes it
   and marks it latest (only if it is the newest version; runs are serialised),
4. when the tag is the newest version, fails unless `/releases/latest` now names it.
   An older tag (a re-run, or a backport) publishes without becoming latest.

**The release is not done until that workflow run is green.** Check it, then, when you
released the newest version, confirm:

```bash
gh api repos/jaylfc/taOS/releases/latest --jq .tag_name   # must print v1.0.0-beta.N
```

This step exists because beta.54 and beta.55 were tagged without a GitHub Release
(2026-09-30 to 2026-10-02): the in-app update check (`tinyagentos/github_releases.py`)
reads `/releases/latest`, which skips tags without a release, so installed hosts kept
seeing beta.53. The taos.my changelog page also pulls from GitHub Releases.

Do NOT create the release as a prerelease: `/releases/latest` skips prereleases.

**Fallback** if the workflow cannot run (Actions outage), publish by hand from the same
notes. Nothing else will attach the bundle in an outage (ci.yml's `release: published`
job needs Actions too), so build and upload it yourself BEFORE making the release public:

```bash
git checkout v1.0.0-beta.N
python3 scripts/changelog_section.py v1.0.0-beta.N > notes.md
(cd desktop && npm ci && npm run build)
tar -C static -czf desktop-bundle.tar.gz desktop
git rev-parse HEAD:desktop > desktop-tree.txt
sha256sum desktop-bundle.tar.gz | awk '{print $1}' > desktop-bundle.sha256
gh release create v1.0.0-beta.N --verify-tag --draft --title "v1.0.0-beta.N" --notes-file notes.md
gh release upload v1.0.0-beta.N desktop-bundle.tar.gz desktop-tree.txt desktop-bundle.sha256
gh release edit v1.0.0-beta.N --draft=false --latest=false
# Mark latest ONLY if no published release is newer than this tag:
python3 -m pip install --quiet packaging   # newest_release_tag.py needs it
newest=$(gh api --paginate "repos/jaylfc/taOS/releases?per_page=100" \
  --jq '.[] | select(.draft == false and .prerelease == false) | .tag_name' \
  | python3 scripts/newest_release_tag.py)
if [ "$newest" = v1.0.0-beta.N ]; then
  gh release edit v1.0.0-beta.N --latest
else
  echo "published, NOT latest: ${newest:-<none>} is newer"
fi
```

## Notes

- The install-count ping reports the installed version per device, so each release bump gives per-build telemetry without any extra work.
- Never tag on `dev`; tags always land on `master` after promotion.
- Hotfixes follow the same steps: bump, changelog, PR to dev, promote, tag.
- **Dependency rule**: adding or changing a dependency in `pyproject.toml` requires regenerating `uv.lock` in the same PR. CI runs `uv lock --check` before the test shards and fails if the two files have drifted apart.
