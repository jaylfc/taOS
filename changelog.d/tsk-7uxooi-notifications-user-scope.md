### Fixed

- Cross-user notification read and mutation: `list`, `list_archived`, `unread_count`, `mark_read`, `archive`, and `mark_all_read` now scope to the authenticated user (`user_id IS NULL OR user_id = ?`), so a user can only see and modify their own notifications plus broadcasts. Previously these endpoints returned every user's rows and allowed cross-user mutations (CWE-862).
- Notification routes resolve the caller from `request.state.user_id` instead of a cookie-only dependency, so local-token (`taosctl notifications`) callers resolve to the primary user and keep working instead of returning 401.
