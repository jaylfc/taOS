### Security

- The Claude Code harness of the taOS Agent no longer runs with `bypassPermissions`: it runs `--permission-mode default` with an `--allowedTools` list (shell, Read, Grep, Glob, Edit and Write confined to the agent workspace) and a `--settings` file whose PreToolUse hook refuses `git push` and any `gh` call, so GitHub writes can only go through the gated taOS tool.
