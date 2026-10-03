### Fixed

- `tinyagentos/update_preflight.py`: `check_preflight()` now coerces its `project_dir`
  argument to `Path` and accepts `str | os.PathLike`, so the update-check route (which
  passes the project dir as a `str`) no longer 500s with
  `'str' object has no attribute 'rglob'` when the preflight reaches the
  foreign-owned-files check.
