### Fixed
- Shell guard: added missing `time` wrapper options (`-f`, `--format`, `-o`, `--output`) and `xargs --process-slot-var` so obfuscated `gh` behind these wrappers is refused
- Shell guard: fixed `env` clustered `-S` handling to only trigger on single-dash short-option clusters (e.g., `-iS`, `-iSls`), not on bare words like `MAKES` or `MyScript.sh`
- Shell guard: unparseable commands now return a specific "could not be parsed" refusal message instead of the generic `git push`/`gh` message
- Shell guard: command-position parameter expansions (`g${X}h`, `${GH}`, `g$X""h`) are now refused; path-prefix expansions (`$HOME/.local/bin/uv`) keep the literal basename