# Release process

taOS uses semver beta: `1.0.0-beta.N`, incremented on every dev->master promotion.

## Steps

### 1. Bump version

Update the version string to the next `1.0.0-beta.N` in exactly these files (keep them identical):

- `pyproject.toml` line `version = "..."`
- `desktop/package.json` line `"version": "..."`
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

### 5. Tag and create a GitHub Release

On `master`, after the merge commit:

```bash
git tag v1.0.0-beta.N
git push origin v1.0.0-beta.N
```

Create a GitHub Release for that tag. Paste the matching CHANGELOG section as the release body:

```bash
gh release create v1.0.0-beta.N --title "v1.0.0-beta.N" --notes-file <notes> --latest
```

Do NOT pass `--prerelease`: betas are our normal releases here, and the in-app
update check (`tinyagentos/github_releases.py`) reads `/releases/latest`, which
skips prereleases. A release created as a prerelease leaves both the GitHub
"Latest" badge and the update check stuck on the previous version.
The taos.my changelog page pulls from GitHub Releases, so this is the canonical public record.

## Notes

- The install-count ping reports the installed version per device, so each release bump gives per-build telemetry without any extra work.
- Never tag on `dev`; tags always land on `master` after promotion.
- Hotfixes follow the same steps: bump, changelog, PR to dev, promote, tag.
