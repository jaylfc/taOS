### Added
- Account-to-account app sharing backend: new `SharingGrantStore` and `/api/sharing/grants*` routes allow creators to privately share apps/games/projects/workflows/studios with specific users by username or email. Recipients see active grants in "Shared apps" section; creators can revoke access at any time.

### Fixed
- Security fix for sharing grants: Different owners can no longer reactivate or hijack each other's grants. Now `grant()` raises `PermissionError` when a different owner attempts to activate a revoked or active grant for the same (artifact_id, grantee) pair.