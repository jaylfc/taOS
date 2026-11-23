### Fixed
- Desktop rebuild now stages builds in a temporary directory and atomically swaps into static/desktop, so browsers never see a missing or half-written bundle during updates.
