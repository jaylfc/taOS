### Fixed

- `OMPAdapter` now forces `command=["omp", "acp"]` when given an `ACPConfig`, instead of silently driving whatever binary the config names. `OMPConfig` subclasses `ACPConfig` with the OMP command as the default so the documented usage (`OMPConfig(session_key="...")`) works without requiring `command` to be supplied.
