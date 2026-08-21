# Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

<!-- Route module `tinyagentos/routes/agents.py`. Owner routes behind the session cookie; no registry scope reaches them -->

## Memory mode values

| value | meaning |
|---|---|
| `both` | framework-native memory AND taOSmd (the default) |
| `framework` | the framework's own memory only |
| `taosmd` | taOSmd only |

## Key points

- `framework` is ADVISORY today, not enforced. The mode tells the agent runtime what to use; it does **not** yet stop the controller from involving taOSmd. A `framework`-mode deploy still registers the agent with taOSmd and still splices taOSmd rules into `AGENTS.md`. So a taOSmd outage can still block a `framework` deploy.

- `memory_mode` is OPTIONAL on `PATCH /api/agents/{slug}/memory` and omitting it leaves the stored value alone. Only `memory_plugin` is required.

- Agents deployed before this field existed are backfilled to `both` by `config.py` when the config loads, so an older agent record without the key reads as the default rather than as empty.

- `POST /api/agents/deploy` takes `memory_mode` on the body, defaulting to `both`. It is persisted on the agent record and injected into the agent's environment as `TAOS_MEMORY_MODE` at deploy time, so the runtime honours it without a second push.

- Deploy validates the pair before any side effect: an unknown `memory_mode` or `memory_plugin` answers `400` naming the valid set, and so does a contradictory pair such as `{"memory_plugin": "none", "memory_mode": "taosmd"}`.