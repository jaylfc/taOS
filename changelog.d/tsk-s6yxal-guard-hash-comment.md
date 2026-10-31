### Fixed

- Fixed shlex comment handling in shell guard: `#` inside a word no longer starts a comment, preventing command dropping (e.g., `echo a#b ; rm x` now correctly parses both commands).