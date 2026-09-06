### Fixed

- Broadcast notifications now have per-user read/archived state to prevent cross-user state leaks. Previously, when one user marked a broadcast notification as read or archived it, it affected all users' inboxes. Now each user's read/archived status is tracked independently in the `notification_user_state` table, preserving individual inbox states while maintaining the shared broadcast nature of the notification.
- Fixed `unread_count` query missing table alias and `list()` query missing per-user archive filter so broadcast state is correctly scoped per user.
