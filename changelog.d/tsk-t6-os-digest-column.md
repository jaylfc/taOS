### Fixed

- A notifications database created by an earlier build of the OS notification route (its id table had no `digest` column) is upgraded on start instead of failing every post with "no such column".
