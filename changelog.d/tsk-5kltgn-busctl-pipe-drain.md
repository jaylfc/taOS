### Fixed
- busctl() now drains pipes after kill in timeout and cancel handlers, preventing hangs when child process floods stdout