### Fixed

- Fixed ownership verification for sharing grants. Changed `_caller_owns_artifact` to fail closed for unverifiable artifact kinds (app, game, workflow, studio) that lack store ownership verification. Projects now require `user_id` match (not `created_by`) for ownership. Removed `For now`, `TODO` and speculative comments. App/game consent records no longer authorize sharing grants.