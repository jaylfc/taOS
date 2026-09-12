### Changed

- The desktop SPA 404 error now distinguishes the two failure modes the user can
  actually act on. When `static/desktop/` exists but has no `index.html`, the
  message remains "Desktop shell not built — run: cd desktop && npm run build".
  When the directory is missing entirely, it now reports "Desktop shell not
  installed (static/desktop missing; not built or staged on this install)"
  instead of blaming the build. This corrects issue #2080, where a non-editable
  install (the bundle is a git-ignored artifact staged at install time by
  `install-server.sh`/`rebuild-desktop.sh` or the CI prebuilt-bundle download)
  told the user to run `npm run build` on a machine with no Node, when the real
  cause was that the bundle was never staged.