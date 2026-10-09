### Fixed

- Inverted the wake agent function alias: the private function `_wake_agent_with_task` is now the primary definition, with the public name `wake_agent_with_task` added as an alias. This restores the correct behavior where tests can monkeypatch the function via `_wake_agent_with_task` and the internal heartbeat tick can call it directly.
