### Added

- taOS Agent GitHub write helper (`tinyagentos/github_writes.py`): `ensure_fork`, `push_branch`, `open_pr` and `create_issue` act as the user with their own token, push only to `taos-agent/<slug>` and open PRs into `dev`, refuse any write whose files or text match a secret shape (private keys, API keys, `.env` contents, IPv4 and Tailscale addresses or hostnames, the data_dir path) before any network call, allow one open taOS Agent PR per repo per user, end PR and issue bodies with an attribution line, and issue no delete, force or merge call.
- A push to a just-created fork waits (bounded) for the fork to become git-ready, and `open_pr` returns the same-branch PR even when another open taOS Agent PR is listed first.
