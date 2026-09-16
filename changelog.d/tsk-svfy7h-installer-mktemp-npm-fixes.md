### Fixed
- Installer mktemp templates no longer carry a suffix after the Xs (busybox/BSD/macOS reject them), fixing all five sites.
- qmd npm install drops removed `--unsafe-perm` flag (hard error on npm >= 11), runs lifecycle scripts explicitly via `--allow-scripts`, and cd's to `/tmp` to avoid a project-level `.npmrc` vetoing the global install.
