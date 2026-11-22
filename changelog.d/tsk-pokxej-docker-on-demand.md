### Fixed

- Docker on-demand install now requires explicit human admin session consent (via=session, is_admin=true). Agent tokens, host local tokens, and non-admin sessions are rejected with 403.
- Docker installation no longer runs from the unprivileged taos service account. Instead, `ensure_docker_on_demand()` returns a `docker_required` response with the exact `sudo` command the owner must run (sourcing `scripts/install-server.sh --ensure-docker`). Honours `TAOS_SKIP_DOCKER=1`.
- Runtime check for `runtime: docker` apps now applies to all install paths (both legacy and resolver-driven), not only the legacy docker branch. Apps with `runtime: either` use LXC by default.
- Store UI shows a "Needs Docker" badge on docker-runtime apps and a consent dialog with the exact install command before proceeding. Added vitests for the badge and dialog.
- Added test coverage: agent token 403, local token 403, non-admin session 403, anonymous 403, admin session 200, and pre-installed Docker proceeds without consent.
