### Added
- Implement dispatcher ledger and backoff store methods: insert_lease, pending_for_agent, recent_expiries, expired_counts_48h, last_assigned_map, stamp_last_assigned in DispatcherStore.
- Rename _wake_agent_with_task to wake_agent_with_task in agent_heartbeat.py and preserve alias for backward compatibility.
