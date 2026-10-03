### Fixed

- Fixed taosmd sys.modules pollution: tests that stub `taosmd` now restore the original module via `patch.dict` instead of popping it, and `taosmd.agents` is resolved with `importlib.import_module` so attribute access on the package is never needed.
