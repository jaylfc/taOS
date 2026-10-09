### Fixed

- Restored archiveRead method to properly archive decided request bells (auth_requests, agent_scope_requests sources) so they land in History after user answers access requests. Without this fix, spa-build failed due to test "archiveRead archives AND marks read (resolved item lands in History)" failing.