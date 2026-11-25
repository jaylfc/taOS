### Fixed

- **Library settings writes are admin-only (#2060)** — `PUT /api/library/settings` now requires an admin session. The settings (preferred quality, storage cap) are library-wide, and previously any signed-in user could change them for everyone; a member's write is now refused 403 before any state changes. Reads stay available to any signed-in user.
