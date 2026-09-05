### Fixed

- Config validation now rejects non-integer values (floats, bools) for global_default, per_agent, and per_project budgets. Previously `0.9` was accepted and truncated to `0`, silently disabling scheduled wakes fleet-wide.
- Damaged wake_budget.json state now reports unknown/damaged state (consumed:0, remaining:0) instead of full budget (consumed:0, remaining:budget) when can_wake returns False.
- Fleet wake-info now preserves the first successful read when the second read fails, maintaining consistency across the two read surfaces.
- Bare/empty `wake_budget:` YAML keys (parsed as ``None``) were supposed to be tolerated as defaults here but the code change did not actually land (the AppConfig constructor still received ``None`` and ``validate_config`` rejected it). The actual fix ships separately; see the tsk-oenmo2 changelog fragment.
