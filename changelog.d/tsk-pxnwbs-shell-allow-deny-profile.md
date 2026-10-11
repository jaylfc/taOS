### Added

- The taOS Agent's Claude Code harness now keeps a per-user shell allow/deny profile. A shell command the user has not decided yet is refused and asked about, both as an inline Allow/Deny card in the taOS Agent chat and in Decisions; the answer becomes a rule for that program, or program plus subcommand for tools like `npm` and `git` (`npm test`, `git status`). Rules are listed, flipped and revoked in Agents > taOS Agent > Shell. Read-only commands start out allowed; `git push` and `gh` stay blocked whatever the profile says.
