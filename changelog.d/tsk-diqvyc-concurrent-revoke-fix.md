### Fixed

- Fixed a race condition where concurrent revoke during cloud_agent member removal would return a 500 error and leave the member listed. The fix catches the ValueError that occurs when a concurrent DELETE /api/agents/registry/{id} flips the record to revoked between the pre-check read and revoke call. If the record is now revoked (or missing), the revoke is treated as done and the member removal continues. Otherwise, the error is re-raised.
