# Per-Agent QMD Serve Setup

Each agent runs its own `qmd serve` instance inside its LXC container. This keeps agent data (memory, embeddings, QMD database) inside the agent's container where it belongs, enabling multi-host fallback and clean separation.

## Architecture

```
Host (Orange Pi / x86)
├── rkllama (port 8080) — shared NPU/GPU inference
├── TinyAgentOS (port 6969) — web GUI, talks to each agent's qmd serve
│
├── LXC: agent-alpha
│   ├── agent framework gateway
│   └── qmd serve (port 7832) → connects to host's rkllama
│       └── ~/.cache/qmd/index.sqlite (agent's memory)
│
├── LXC: agent-beta
│   ├── agent framework gateway
│   └── qmd serve (port 7832) → connects to host's rkllama
│       └── ~/.cache/qmd/index.sqlite
│
└── LXC: agent-gamma
    ├── agent framework gateway
    └── qmd serve (port 7832) → connects to host's rkllama
        └── ~/.cache/qmd/index.sqlite
```

**Key point:** Each agent's `qmd serve` uses the shared rkllama/ollama backend for inference but stores its own index database locally. TinyAgentOS accesses each agent's memory via the agent's `qmd_url`.

## Install QMD in Agent LXC

```bash
# Inside the agent's LXC container
# Pin to a known-good version to avoid surprise breakage on fresh
# installs.  The npm package is pre-built; installing from the git
# source requires a TypeScript build step.
npm install -g @jaylfc/qmd@2.1.1
```

## Configure QMD to Use Remote Backend

Set the `QMD_SERVER` environment variable so the QMD CLI uses the remote model server for inference, but keep the index database local:

```bash
# The agent's qmd serve connects to rkllama on the host for inference
# but stores its index in ~/.cache/qmd/index.sqlite locally
export QMD_SERVER=http://<host-ip>:7832  # for CLI operations
```

## Start QMD Serve in Agent LXC

Each agent runs its own `qmd serve` that:
1. Serves its local index database via HTTP (search, browse, collections, status)
2. Routes inference requests (embed, rerank, expand) to the shared rkllama backend on the host

```bash
qmd serve --port 7832 --bind 0.0.0.0 --backend rkllama --rkllama-url http://<host-ip>:8080
```

Replace `<host-ip>` with the host's IP address. If using Tailscale, the Tailscale IP avoids macvlan routing issues where LXC containers can't reach the host's LAN IP.

## Systemd Service (Per Agent LXC)

Create `/etc/systemd/system/qmd-serve.service`:

```ini
[Unit]
Description=QMD Model Server (Agent Memory)
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/qmd serve --port 7832 --bind 0.0.0.0 --backend rkllama --rkllama-url http://<host-ip>:8080
Restart=on-failure
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now qmd-serve
```

## TinyAgentOS Config

In TinyAgentOS's `data/config.yaml`, point each agent to its QMD serve instance:

```yaml
agents:
  - name: agent-alpha
    host: 10.0.0.10
    qmd_url: http://10.0.0.10:7832
    color: "#98fb98"
  - name: agent-beta
    host: 10.0.0.11
    qmd_url: http://10.0.0.11:7832
    color: "#ffd700"
  - name: agent-gamma
    host: 10.0.0.12
    qmd_url: http://10.0.0.12:7832
    color: "#ff7eb3"
```

TinyAgentOS then queries each agent's endpoints:
- `GET /status` — index health
- `GET /collections` — list memory collections
- `GET /search?q=X` — keyword search
- `GET /browse?limit=20` — paginated browsing
- `GET /health` — backend status

## Verify

From the host, test each agent's QMD serve:

```bash
# Check agent's memory status
curl http://10.0.0.10:7832/status

# Search agent's memory
curl "http://10.0.0.10:7832/search?q=meeting+notes"

# Browse recent chunks
curl "http://10.0.0.11:7832/browse?limit=5"

# Check collections
curl http://10.0.0.12:7832/collections
```

## Embedding Content

To add content to an agent's memory, run QMD commands inside the agent's LXC:

```bash
# Inside the agent's LXC
qmd collection add ~/workspace --name workspace
qmd embed
```

The embedding process uses the remote rkllama backend (via the `--backend rkllama` flag on qmd serve), but stores the vectors in the local SQLite database.
