# openclaw install script

`install.sh` runs once inside a fresh Debian bookworm LXC container to install the
upstream OpenClaw agent runtime from npm.

## What it does

1. Installs Node.js >=22.19 via NodeSource if not already present (full version check,
   not just major — earlier 22.x releases are too old).
2. Runs `npm install -g openclaw@latest` to install upstream OpenClaw.
3. Writes `/root/.openclaw/openclaw.json` (gateway config) and `/root/.openclaw/env`
   (env file) from env vars injected by the deployer.
4. Installs a system-level `openclaw.service` systemd unit that runs `openclaw gateway`.
5. Enables the service (but does NOT start it — the deployer starts it after writing
   the LiteLLM key to prevent a crash-loop before the key is available).
6. Installs `trash-cli` and the taOS shadow `rm` wrapper (recycle-bin layer).

The gateway listens on port 18789 (loopback bind by default as set in openclaw.json).
taOS drives agent turns via ACP (`openclaw_acp_runtime.py`), not via the gateway WS.

## Env vars consumed (injected by the deployer at container creation time)

| Var | Purpose |
|---|---|
| `TAOS_AGENT_NAME` | Agent slug |
| `TAOS_MODEL` | Primary model ID (e.g. `kilo-auto/free`) |
| `TAOS_FALLBACK_MODELS` | Comma-separated fallback model IDs |
| `LITELLM_API_KEY` | Per-agent LiteLLM virtual key |
| `OPENAI_API_KEY` | Same as LITELLM_API_KEY; kept for compat |
| `OPENAI_BASE_URL` | LiteLLM `/v1` root on the host (default `http://127.0.0.1:4000/v1`) |
| `TAOS_BRIDGE_URL` | taOS API root (default `http://127.0.0.1:6969`) |
| `TAOS_LOCAL_TOKEN` | Per-container bearer token for taOS API calls |

## Debugging

```bash
incus exec taos-agent-<name> -- bash
journalctl -u openclaw -f
openclaw health        # exits non-zero if gateway unreachable
```

## Notes

- The old Python/FastAPI stub (`/opt/openclaw/server.py`, port 8100) was replaced by
  this upstream npm install. Do not refer to that stub in new code or docs.
- `install.sh` is idempotent — safe to re-run on an already-provisioned container.
