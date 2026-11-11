### Fixed
- Hub block route now reports a 500 `block-incomplete` response when the contacts-store cascade fails, so callers do not treat a failed peer-link revoke as a successful block.
