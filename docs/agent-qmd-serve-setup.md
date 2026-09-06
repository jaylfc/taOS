# QMD Memory Service Setup

taOS uses a single shared `qmd serve` process on the host for all agent memory
operations — embedding, semantic search, keyword search, browse, and collection
management. There is no per-agent QMD instance; every agent reads and writes
through the shared host service, addressed by agent name and dbPath routing.

## Architecture

```
Host (Orange Pi / x86)
├── rkllama (port 7833) — shared NPU/GPU inference
├── qmd serve (port 7832) — shared memory service
│   ├── data/agent-memory/agent-alpha/index.sqlite
│   ├── data/agent-memory/agent-beta/index.sqlite
│   ├── data/agent-memory/agent-gamma/index.sqlite
│   └── data/user-qmd-index/index.sqlite   (taOS user memory)
├── taOS (port 6969) — web app, routes memory ops through qmd serve
│
├── LXC: agent-alpha
│   ├── agent framework gateway
│   └── /memory → host:data/agent-memory/agent-alpha/  (bind mount)
│
├── LXC: agent-beta
│   ├── agent framework gateway
│   └── /memory → host:data/agent-memory/agent-beta/   (bind mount)
│
└── LXC: agent-gamma
    ├── agent framework gateway
    └── /memory → host:data/agent-memory/agent-gamma/   (bind mount)
```

**Key point:** One shared `qmd serve` on the host handles all agents. Per-agent
isolation comes from `dbPath` routing — each request specifies which SQLite
file to operate on. taOS resolves `dbPath` to `data/agent-memory/{name}/index.sqlite`.

## Install QMD on the Host

```bash
# On the host — a single qmd installation serves all agents
npm install -g @jaylfc/qmd@latest
```

## Configure QMD to Use Remote Backend

Set the `QMD_SERVER` environment variable so the QMD CLI uses the remote model
server for inference. The index databases live under `data/agent-memory/`.

```bash
# The host qmd serve connects to rkllama/ollama for inference
export QMD_SERVER=http://localhost:7833  # for CLI operations
```

## Start QMD Serve on the Host

A single `qmd serve` runs on the host:

```bash
qmd serve --port 7832 --bind 0.0.0.0 --backend rkllama --rkllama-url http://localhost:7833
```

## Systemd Service (Host)

Create `/etc/systemd/system/qmd-serve.service`:

```ini
[Unit]
Description=QMD Memory Service (shared host-level)
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/qmd serve --port 7832 --bind 0.0.0.0 --backend rkllama --rkllama-url http://localhost:7833
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

## taOS Config

In taOS's `data/config.yaml`, configure the shared qmd serve URL at the top
level. Per-agent `qmd_url` has been removed from the agent schema.

```yaml
qmd:
  url: http://localhost:7832

agents:
  - name: agent-alpha
    host: 10.0.0.10
    color: "#98fb98"
  - name: agent-beta
    host: 10.0.0.11
    color: "#ffd700"
  - name: agent-gamma
    host: 10.0.0.12
    color: "#ff7eb3"
```

taOS routes all memory operations through the shared `qmd.url` with agent-name
based dbPath isolation:

- `POST /api/memory/search` — keyword or semantic search (agent-aware)
- `GET /api/memory/browse` — paginated browsing (agent-aware)
- `GET /api/memory/collections/{agent_name}` — list collections
- `DELETE /api/memory/chunk/{hash}` — delete by hash (agent-aware)
- `POST /api/import/embed` — ingest files into agent memory

## Agent Container Bind Mounts

The deployer bind-mounts each agent's memory directory so the agent and the host
see identical state:

```
data/agent-memory/agent-alpha/ → /memory (inside agent-alpha LXC)
data/agent-memory/agent-beta/  → /memory (inside agent-beta LXC)
```

## Firewall (shared A2A bus hosts)

When a host runs `taosmd serve` as the shared A2A bus (default port 7900),
remote agents and workers need inbound TCP access to that port. If ufw is
active the port is blocked by default; the install script opens it
automatically, but you can also do it by hand:

```bash
sudo ufw allow 7900/tcp comment 'taOS A2A bus'
sudo ufw status | grep 7900
```

If the bus port was changed via `TAOS_BUS_PORT`, substitute that value.

## Verify

From the host, test the shared qmd serve:

```bash
# Check memory service status
curl http://localhost:7832/status

# Search an agent's memory (dbPath is resolved by taOS routes)
curl "http://localhost:7832/search?q=meeting+notes&dbPath=data/agent-memory/agent-alpha/index.sqlite"

# Browse recent chunks for an agent
curl "http://localhost:7832/browse?limit=5&dbPath=data/agent-memory/agent-alpha/index.sqlite"

# Check collections for an agent
curl "http://localhost:7832/collections?dbPath=data/agent-memory/agent-beta/index.sqlite"
```

## Embedding Content

Content is embedded through taOS routes (`POST /api/import/embed`) or directly
via the qmd serve:

```bash
# Direct ingest via qmd serve
curl -X POST http://localhost:7832/ingest \
  -H "Content-Type: application/json" \
  -d '{"body": "content to embed", "title": "note", "collection": "knowledge", "dbPath": "data/agent-memory/agent-alpha/index.sqlite"}'
```
