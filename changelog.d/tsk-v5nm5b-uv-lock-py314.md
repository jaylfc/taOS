### Fixed
- uv.lock regenerated for the widened `requires-python = ">=3.11,<3.15"`, so `uv sync --frozen --python 3.14` installs instead of refusing with "not compatible with the locked Python requirement". No package versions changed; the lock gains cp314 wheels only.
- New offline guard `test_uv_lock_requires_python_matches_pyproject` fails whenever uv.lock's recorded Python bound disagrees with pyproject.toml.
