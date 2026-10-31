### Added

- Added admin-only checks to hub routes (GET /api/hub/timeline, GET /api/hub/chain, GET /api/hub/blobs/{blob_hash}, POST /api/hub/posts/{post_hash}/delete) to prevent non-admin members from accessing owner's private hub content (friends-only posts and blobs) and deleting owner's posts.
