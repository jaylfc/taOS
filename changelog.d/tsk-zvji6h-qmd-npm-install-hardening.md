### Fixed
- install-server.sh: the `--allow-scripts` glob argument for qmd npm install is now single-quoted at both the pinned-version site and the `@latest` ETARGET retry, preventing a malicious filename in `/tmp` from widening which qmd dependencies run lifecycle scripts as root.
- install-server.sh: qmd npm install now `cd`s into a root-owned `mktemp -d` temp dir instead of the world-writable `/tmp`, closing the open project-config surface that `.npmrc` in a local user's checkout could reach via cwd.
