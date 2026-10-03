### Fixed

- `tests/test_update_preflight.py`: the foreign-owned-files test now patches `Path.stat` so only the planted file reports the root-owned stat, so it also passes on Python 3.12, where `rglob` stats the scanned directory itself.
- `changelog.d/tsk-fdbzpe-fakeproc-returncode-sweep.md`: trimmed to changelog bullets so it satisfies the doc gate.
