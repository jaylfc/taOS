### Fixed
- Generated images and music are now stored per-user under `data_dir/workspace/users/<user_id>/images|music/generated` instead of a shared directory.
- The `/data/workspace` static file mount is replaced with an authenticated route that enforces ownership checks for user-scoped paths (`/data/workspace/users/<uid>/...`) via `require_owner_or_admin`, while legacy shared paths remain session-gated only.
- Path traversal is prevented via `.resolve()` + `is_relative_to()` validation.
- Frontend apps (DesignStudio, MusicStudio) now use server-provided user-scoped paths for generated media.