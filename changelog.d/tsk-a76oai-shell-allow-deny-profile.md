### Added

- The taOS Agent's Claude Code harness now keeps a per-user shell allow/deny profile. A shell command the user has not decided yet is refused and asked about, both as an inline Allow/Deny card in the taOS Agent chat and in Decisions; the answer becomes a rule for that program, or program plus subcommand for tools like `npm` and `git` (`npm test`, `git status`). Rules are listed, flipped and revoked in Agents > taOS Agent > Shell. Writing a file with `>` and git config overrides (`git -c`) are rules of their own. Read-only commands start out allowed; `git push` and `gh` stay blocked whatever the profile says.

### Fixed

- The shell guard now detects `--output` / `--output=` after a git subcommand and `sort -o` / `sort -o=` / `sort -o<file>` as output redirections, requiring the `output-redirect` rule.
- The shell guard now refuses `sort --compress-program` (and `--compress-prog=`, `--com=` prefixes), treats any unambiguous `--output` prefix for `sort` as output redirection, detects clustered short options containing `o` for `sort` (e.g. `-ro`), and treats the second positional argument of `uniq` as output redirection.
- Fix `uniq --` positional counting: the first positional after `--` is input, the second is output (was off by one). Fix `sort` clustered short options: always skip next token when `o` is present (e.g. `-or`), not only when `o` is last.
- Fix `uniq` positional counting with attached option values (`-f1`, `-s2`, `-w3`, `-cf1`), `--` resetting positionals, bare `-` as stdin positional, and long options `--skip-fields`, `--skip-chars`, `--check-chars` with separate values; the second positional is now correctly detected as output redirection in all cases.
- A queued taOS Agent chat request no longer replaces the active turn's shell allow/deny rules; the profile is loaded only once a turn is accepted.
