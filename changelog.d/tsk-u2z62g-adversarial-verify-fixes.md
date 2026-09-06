### Fixed

- The adversarial-verify stage in `tinyagentos/code_analyzer.py` now tracks inline and multi-line block comments using lexical state instead of line-prefix heuristics.
- Hardcoded-secret findings are no longer suppressed by generic string-literal filtering; only the known-exception allowlist applies.
- Network-exfil adversarial verification inspects the actual detector match span rather than the first trigger token on the line, preventing inert earlier tokens from suppressing real later findings.
- String-literal classification now uses a proper lexical scanner that handles escaped quotes and backtick template literals, eliminating false positives from mixed-quote lines.
- The block-comment mask is now a start-of-line state resolved forward to the finding's own match position, so real code after a same-line `*/` (and a dangerous call before a trailing `/* ... */`) is no longer silently dropped. Comment markers inside string literals or after `//` no longer open a block comment for the lines that follow.
- Detector match spans starting at column 0 are honoured instead of being treated as "no span", so a finding at the very start of a line is classified from its own span rather than from an unrelated trigger token later on the line.
- The known-example allowlist for hardcoded secrets is scoped to the finding's match span, so a real key sharing a line with a documented example value is still reported; the secret detector now reports every match on a line rather than only the first.
- The block-comment mask is built once per file instead of once per finding, removing the O(findings x lines) rescan from the app install/publish path.
