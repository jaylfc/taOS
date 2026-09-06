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
- Start returns a one-shot VNC password, mode-600 file not argv; a secret
  left behind fails the start (no password).
- `status` 500s, records the error, keeps state; `running` is `null` then,
  not `false`.
