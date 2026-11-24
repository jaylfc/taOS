### Fixed
- In-app Install update now records the rollback target before mutating the working tree, so `taos rollback` can restore the pre-update state after the most common update path.
