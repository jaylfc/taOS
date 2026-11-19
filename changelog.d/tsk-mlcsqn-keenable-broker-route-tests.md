### Fixed

- Keenable broker route tests now use the `client` fixture and create an active Keenable grant before asserting 400s, plus new success-path tests mocking `keenable_search` and `keenable_fetch`, 403 tests for no token, no grant and wrong scope, a 409 test when Keenable is not configured, and a `num_results: true` boolean rejection test
