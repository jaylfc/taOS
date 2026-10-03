### Fixed

- Follow Docker Hub's `next` link and accumulate valid tag names across pages (bounded to 10 pages) in `_fetch_docker_hub_tags` (tinyagentos/upstream_versions.py:230-269). This ensures that when the newest eligible tag is on a later page, check_upstream selects from a complete list instead of an incomplete one.

- Use `upstream_versions.pinned_tag(app)` as the comparison baseline first, fall back to the recorded pin, then `app.version` in `_upstream_baseline` (tinyagentos/routes/store.py:77-92). This ensures that after a same-shape catalog bump, the stale recorded pin doesn't make an update look already applied or still pending.

- Added tests to verify both fixes:
  - `test_docker_hub_tags_follow_next_page`: Tests that Docker Hub pagination is properly followed
  - `test_baseline_prefers_current_pin`: Tests that the current pin is preferred over the recorded pin in the baseline