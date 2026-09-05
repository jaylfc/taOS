### Security

- Theme (`.taostheme`) installs and backup restores are now guarded against archive bombs: a shared `safe_archive` helper caps the declared uncompressed total, per-member size and member count before anything is read or written, theme uploads over 32 MB and backup uploads over 64 MB are refused with `413`, and a backup tarball is now extracted under the path-safe tar filter (an unsafe member fails the restore instead of being silently skipped).
