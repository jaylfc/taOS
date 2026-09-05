### Security

- Theme (`.taostheme`) installs and backup restores are now guarded against archive bombs: a shared `safe_archive` helper caps the declared uncompressed total, per-member size and member count before anything is read or written, and a backup tarball is now extracted under the path-safe tar filter (an unsafe member fails the restore instead of being silently skipped). A tarball's headers are judged one at a time as they arrive, so a compressed member over the cap is rejected without being decompressed first.
- Theme, backup and userspace-app uploads (32 MB, 64 MB and 64 MB) are refused with `413` while the request body is still arriving, so an oversized upload is no longer spooled to temporary storage before the handler sees it.
- A `.taostheme` member resolving to the theme directory itself (for example `.`) is now rejected as an unsafe path instead of crashing the install.
