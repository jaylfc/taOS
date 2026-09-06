### Fixed
- MessagesApp sidebar presence now assembles bound channels through the shared `collectBoundChannels` helper, and the standalone project-channel regression test binds to that call instead of a hand-built copy, so it genuinely covers the production path
