# Agent desktop lifecycle (install, start, stop, status)

<!-- Route module `tinyagentos/routes/agent_desktop.py`. Owner routes behind the session cookie; no registry scope reaches them -->

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/{agent_name}/desktop/install` | Install XFCE + x11vnc into the agent container |
| `POST` | `/api/agents/{agent_name}/desktop/start` | Start the XFCE desktop + VNC server |
| `POST` | `/api/agents/{agent_name}/desktop/stop` | Stop the running desktop session |
| `GET` | `/api/agents/{agent_name}/desktop/status` | Report install and runtime state |

## States

| state | meaning |
|---|---|
| `not_installed` | desktop packages not yet installed |
| `installed` | packages installed, session not running |
| `starting` | start in progress |
| `running` | x11vnc is listening on :5900 inside the container |
| `stopping` | stop in progress |
| `stopped` | packages installed but no session running |
| `error` | last operation failed |

## Key points

- Install is **on demand** and **opt-in per agent**. It does not change the default agent image or boot time.
- Start is idempotent: calling start on a running desktop returns 200 without side effects.
- Stop is idempotent: calling stop on a stopped desktop returns the current state.
- Status probes the container for the `x11vnc` process when the cached state says running; if the process is absent it resets the cached state to `stopped`.
