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
- The VNC password never reaches a command line: it travels as a mode-600
  file that is pushed in, read by `vncpasswd -f`, and deleted on both sides.
- `status` returns 500 and leaves the tracked state unchanged when the probe
  itself could not run; it only records `stopped` on a probe that answered.