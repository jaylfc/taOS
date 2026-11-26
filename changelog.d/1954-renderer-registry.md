### Added

- **taOStalk content-block renderer registry (#1954, slice 2)** — chat content blocks now dispatch through a pluggable per-kind renderer registry instead of a hard-coded switch, and five block types render for the first time: `code` (via the shared CodeBlock), `diff` (unified-diff viewer with per-line classification and add/remove counts), `table` (structured headers/rows with the markdown table styling), and `image`/`file` (adapted onto the existing attachment gallery, lightbox included). Unknown kinds keep the muted "unsupported block" fallback.
