### Changed
- Clarify the re-raise condition when revoking a cloud agent identity during member removal in project membership routes.

### Fixed
- Strengthen catalog index, orb catalog, and agent deletion route tests to assert concrete behavior instead of only route existence.
- Exercise concurrent hub post appends with separate `HubStore` instances to cover cross-instance races.
