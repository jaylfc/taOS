# Agent desktop lifecycle

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/{agent_name}/desktop/install` | Install XFCE + x11vnc |
| `POST` | `/api/agents/{agent_name}/desktop/start` | Start desktop + VNC |
| `POST` | `/api/agents/{agent_name}/desktop/stop` | Stop desktop |
| `GET` | `/api/agents/{agent_name}/desktop/status` | Report runtime state |

## Key points

- Install is on demand, per agent, and retryable.
- Owner or admin only. Start returns a one-shot VNC password.