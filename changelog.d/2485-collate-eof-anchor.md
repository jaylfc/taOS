### Fixed

- **Changelog collator can no longer delete fragments without folding them (#2485 item 7)** — if `CHANGELOG.md` ended exactly at the `## [Unreleased]` anchor (no trailing newline), the insertion silently no-opped, the script reported success, and every fragment was unlinked unfolded. The collator now normalises that file ending before inserting and verifies the new section landed before consuming any fragment.
