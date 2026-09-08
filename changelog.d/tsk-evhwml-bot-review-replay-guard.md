### Added
- Real-body replay guard for bot-review gate: fixtures pulled from live CodeRabbit comment bodies on merged PRs (#2482, #2870, #2871, #2873, #2890) and a test that loads every fixture and asserts the expected verdict (PASS for zero-finding walkthroughs, FAIL for rate-limit stubs).
