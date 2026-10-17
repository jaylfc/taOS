### Fixed

- Added ownership verification for sharing grants. The `create_grant` route now checks that the authenticated user owns the artifact they're trying to grant access to, preventing unauthorized sharing of apps, games, projects, workflows, and studios. This fixes the security gap identified in PR #3656 where any authenticated user could create grants on artifacts owned by other users.