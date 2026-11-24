### Fixed

- **Installer: retry apt downloads and whole pip runs (#2088)** — the
  first #2088 pass added retry flags to pip and to the key-download
  curls, but apt had no equivalent: a `.deb` download dropped
  mid-transfer still failed the step outright, including the incus
  install from the original report. Every `apt-get update`/`install`
  in `install-server.sh` now carries `-o Acquire::Retries=3` (via a
  shared `APT_RETRY` array; the pre-bash bootstrap uses the literal
  option), so a truncated download is retried inside the same apt
  run. The controller-deps pip pair is additionally wrapped in a
  bounded `retry` helper (3 attempts, growing backoff) that re-runs
  only when the failed attempt's output carries a network-drop
  marker (`IncompleteRead`, `ProtocolError`, a timeout, a broken
  connection) -- a dependency conflict or a failed source build
  fails on the first attempt instead of being retried pointlessly:
  pip's
  `--retries` covers connection setup, but a wheel truncated
  mid-body (`IncompleteRead`, the original report's headline
  failure) still aborts the run, and re-running is cheap because
  completed downloads come from the cache. The bootstrap self
  re-fetch gained curl retry flags, and the download-failure
  messages at these steps now say that re-running the installer is
  safe. Retries stay network-shaped: apt retries failed downloads,
  not unsatisfiable dependencies, and a pip failure still aborts
  the install once the attempts are exhausted.
