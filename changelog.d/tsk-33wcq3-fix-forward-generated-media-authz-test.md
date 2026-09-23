### Added
- Member-vs-member authorization test coverage for generated media isolation: non-admin users (bob) are now tested to be denied (403) when accessing another member's (alice) user-scoped generated images and music paths.

### Fixed
- Removed stale legacy-path fallbacks in DesignStudioApp and MusicStudioApp; frontend now exclusively uses server-provided user-scoped paths returned by generate/compose endpoints.