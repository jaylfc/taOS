# Release process

taOS uses semver beta: `1.0.0-beta.N`, incremented on every dev->master promotion.

## Steps

### 1. Bump version

Update the version string to the next `1.0.0-beta.N` in exactly these three files (keep them identical):

- `pyproject.toml` line `version = "..."`
- `desktop/package.json` line `"version": "..."`
- `tinyagentos/__init__.py` line `__version__ = "..."`

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

### 5. Tag and create a GitHub Release

On `master`, after the merge commit:

```
git tag v1.0.0-beta.N
git push origin v1.0.0-beta.N
```

Create a GitHub Release for that tag. Paste the matching CHANGELOG section as the release body.
The taos.my changelog page pulls from GitHub Releases, so this is the canonical public record.

## Notes

- The install-count ping reports the installed version per device, so each release bump gives per-build telemetry without any extra work.
- Never tag on `dev`; tags always land on `master` after promotion.
- Hotfixes follow the same steps: bump, changelog, PR to dev, promote, tag.
