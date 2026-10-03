### Fixed

- SearXNG no longer installs a stale release: the catalog manifest tracks
  upstream `searxng/searxng:latest` instead of pinning `2024.12.0`, and a
  floating image is now generated with `pull_policy: always`, so `docker
  compose up` pulls the newest image before starting the container and every
  install and Store update lands on the current upstream release. The shipped
  `settings.yml` also enables the `bing` engine (measured from a home IP on
  2026-10-02, google/duckduckgo/brave/startpage/qwant/yahoo return 0 results or
  a CAPTCHA while bing works but ships disabled upstream), so a fresh install
  actually returns results; json output stays enabled for agents.