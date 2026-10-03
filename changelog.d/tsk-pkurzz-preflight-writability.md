### Fixed

- Updater preflight: root installs (install.sh User=root) can now update. The foreign-file check uses os.access writability instead of ownership, and returns (0, []) for geteuid()==0.
- check_preflight is offloaded to a thread via asyncio.to_thread so it does not block the event loop in check_for_updates and apply_update routes.
- git ls-remote now runs with cwd=project_dir and only reports branch_not_on_origin when origin is reachable but the ref is missing.
- Branch name validation uses git check-ref-format --branch instead of str.isalnum so names like "release-1.0" and "feature/x" are accepted.
- Removed dead auto_repair_narrow_refspec function and replaced the repair text with the manual git config command.
- .git trees are now pruned in the foreign-file walk so packfiles and loose objects (mode 0444) are not counted as foreign-owned.
- Branch validation now distinguishes git missing (rc < 0) from an invalid branch name (rc != 0) for accurate error messages.
- foreign_owned_files repair text updated to mention chmod/chown, matching the writability semantics.
