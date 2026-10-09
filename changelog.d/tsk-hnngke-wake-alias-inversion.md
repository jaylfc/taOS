### Fixed
- keep _wake_agent_with_task as the def, add the public name as an alias

Inverts the rename that caused deleted-symbols gate failures and broke wake-budget tests by having the public name call the private def instead of the other way around. The tests monkeypatch `tinyagentos.agent_heartbeat._wake_agent_with_task` expecting it to be the primary function that gets called, but the tick was calling the public alias instead.

This restores the correct behavior where:
- The private `_wake_agent_with_task` function remains the primary definition
- The public `wake_agent_with_task` name is added as an alias
- Tests can monkeypatch `_wake_agent_with_task` and intercept the calls
- The deleted-symbols gate passes by keeping the private def as the primary definition
- All wake-budget tests pass
- The test in `test_wake_agent_with_task_public_name_and_private_alias_are_the_same_function` continues to pass

Fixes: #3610 (FF-1): deleted-symbols + wake-budget tests red