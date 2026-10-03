# Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

<!-- Route module `tinyagentos/routes/agents.py`. Owner routes behind the session cookie; no registry scope reaches them -->

## Memory mode values

| value | meaning |
|---|---|
| `both` | framework-native + taOSmd (default) |
| `framework` | framework's own memory only |
| `taosmd` | taOSmd only |

## Key points

- `framework` is ADVISORY, not enforced: tells runtime what to use but doesn't stop controller from involving taOSmd. `framework`-mode deploy still registers with taOSmd, splices rules into `AGENTS.md`, so taOSmd outage can still block.
- `memory_mode` OPTIONAL on `PATCH /api/agents/{slug}/memory`; omit = leave stored value. Only `memory_plugin` required.
- Pre-field agents backfilled to `both` by `config.py` on load.
- `POST /api/agents/deploy` takes `memory_mode` (default `both`), persisted on record, injected as `TAOS_MEMORY_MODE` at deploy.
- Deploy validates first: unknown `memory_mode`/`memory_plugin` → `400` naming valid set; contradictory pair (e.g. `{"memory_plugin": "none", "memory_mode": "taosmd"}`) → `400`.