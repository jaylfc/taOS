### Fixed
- Skip-only tests gate now treats test files that error at collection time (pytest exit codes 2, 3, 4, or 0 tests collected without a module-level skip) as violations instead of silently passing them as clean.
