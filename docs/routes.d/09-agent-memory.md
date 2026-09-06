# Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

<!-- Route module `tinyagentos/routes/agents.py`. Owner routes behind the session cookie; no registry scope reaches them -->

## Memory mode values

| value | meaning |
|---|---|
| `both` | framework-native memory AND taOSmd (the default) |
| `framework` | the framework's own memory only |
| `taosmd` | taOSmd only |

## Key points

- `framework` is ADVISORY today, not enforced: it tells the agent runtime what to use but does **not** yet stop the controller from involving taOSmd. A `framework`-mode deploy still registers with taOSmd and splices taOSmd rules into `AGENTS.md`, so a taOSmd outage can still block it.

- `memory_mode` is OPTIONAL on `PATCH /api/agents/{slug}/memory`; omitting it leaves the stored value alone. Only `memory_plugin` is required.

- Agents deployed before this field existed are backfilled to `both` by `config.py` on config load, so an older record reads as the default rather than as empty.

- `POST /api/agents/deploy` takes `memory_mode` (default `both`); it is persisted on the agent record and injected into the agent's environment as `TAOS_MEMORY_MODE` at deploy time.

- Deploy validates before any side effect: an unknown `memory_mode` or `memory_plugin` answers `400` naming the valid set, as does a contradictory pair such as `{"memory_plugin": "none", "memory_mode": "taosmd"}`.