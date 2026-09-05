# Agent desktop lifecycle

## Routes

Under `/api/agents/{agent_name}/desktop/`:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `install` | Install XFCE + x11vnc |
| `POST` | `start` | Start desktop + VNC |
| `POST` | `stop` | Stop desktop |
| `GET` | `status` | Runtime state |

## Key points

- On demand, per agent, retryable. Owner or admin only.
- Start returns a one-shot VNC password, pushed in as a mode-600 file, not argv.
- `status` 500s and keeps its state when the probe itself could not run.
