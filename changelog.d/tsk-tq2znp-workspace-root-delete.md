### Security

Fixed a path-resolution gap that let `DELETE /api/workspace/files/` (and the
agent workspace and project files mirrors) move the entire workspace root into
the trash. `_resolve_safe` only checked that the target was inside the base, so
the base itself was accepted — an empty path, `.`, `%2e`, or `sub/..` all
normalised to the root and trashed it. A new `_resolve_safe_child` helper refuses
the base itself and is used by every destructive or mutating route (delete,
rename/move source and destination); listing the base still uses
`_resolve_safe`, so behaviour for valid child paths is unchanged.