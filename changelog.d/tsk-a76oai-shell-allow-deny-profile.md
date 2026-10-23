### Fixed

- The shell guard now detects `--output` / `--output=` after a git subcommand and `sort -o` / `sort -o=` / `sort -o<file>` as output redirections, requiring the `output-redirect` rule.
- The shell guard now refuses `sort --compress-program` (and `--compress-prog=`, `--com=` prefixes), treats any unambiguous `--output` prefix for `sort` as output redirection, detects clustered short options containing `o` for `sort` (e.g. `-ro`), and treats the second positional argument of `uniq` as output redirection.
- Fix `uniq --` positional counting: the first positional after `--` is input, the second is output (was off by one). Fix `sort` clustered short options: always skip next token when `o` is present (e.g. `-or`), not only when `o` is last.
- Fix `uniq` positional counting with attached option values (`-f1`, `-s2`, `-w3`, `-cf1`), `--` resetting positionals, bare `-` as stdin positional, and long options `--skip-fields`, `--skip-chars`, `--check-chars` with separate values; the second positional is now correctly detected as output redirection in all cases.
- When a queued Claude Code request overwrites the active turn's shell rules, the active turn runs under the queued user's shell profile instead of its own.
