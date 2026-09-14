### Fixed

- `dompurify` is now a direct `dependencies` entry so the Browser reader-mode sanitizer resolves consistently instead of relying on hoisting from the `overrides` constraint on the `mermaid` transitive tree.
- Added a class-guard test that asserts every bare specifier imported by `desktop/src/**/*.{ts,tsx}` is declared in `dependencies` or `devDependencies` (overrides do not count).
