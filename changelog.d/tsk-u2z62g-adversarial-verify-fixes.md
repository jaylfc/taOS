### Fixed

- The adversarial-verify stage in `tinyagentos/code_analyzer.py` now tracks inline and multi-line block comments using lexical state instead of line-prefix heuristics.
- Hardcoded-secret findings are no longer suppressed by generic string-literal filtering; only the known-exception allowlist applies.
- Network-exfil adversarial verification inspects the actual detector match span rather than the first trigger token on the line, preventing inert earlier tokens from suppressing real later findings.
- String-literal classification now uses a proper lexical scanner that handles escaped quotes and backtick template literals, eliminating false positives from mixed-quote lines.
