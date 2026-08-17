### Fixed

- Closing a claimed task is refused unless you are the claim holder, the project lead, the project owner, or a session admin; any other caller now gets 409 instead of silently closing someone else's card (#2287).
