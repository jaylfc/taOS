### Added
- CI gate that fails PRs where every test in a touched test file skips, naming the file, skip count, and the guard that caused it. A `Tests-Skipped-Intentionally:` trailer in the PR body waives the check, making deliberate skip-only landing a conscious act.
