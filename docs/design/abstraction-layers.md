# Abstraction Layers — Channel Hub, LLM Proxy, Config Injection

**Date:** 2026-04-06
**Status:** Approved
**Amended:** 2026-04-11 — every abstraction in this document now follows the
**backend-driven discovery** principle: availability queries (is model X
loaded? is connector Y online? is capability Z routable?) are answered by
probing the live subsystem, not by reading the filesystem or static config.
Manifests describe potential; live probes describe reality. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md) for
the canonical treatment of this principle.

## Overview

Three abstraction layers that make agent frameworks interchangeable. TinyAgentOS owns all external integrations (messaging, models, secrets). Frameworks become stateless compute engines that can be hot-swapped without losing channels, models, keys, or memory.

```
External Services          TinyAgentOS Platform              Agent Frameworks
─────────────────          ──────────────────────             ─────────────────
Telegram      ─┐           ┌─── Channel Hub ───┐             ┌─ Adapter ─ SmolAgents
Discord       ─┤           │  Owns connections  │             ├─ Adapter ─ PicoClaw
Slack         ─┤──────────▶│  Routes messages   │────────────▶├─ Adapter ─ OpenClaw
Email         ─┤           │  Translates rich   │             ├─ Adapter ─ PocketFlow
Web Chat      ─┘           └────────────────────┘             └─ Adapter ─ Any framework

ollama        ─┐           ┌─── LLM Proxy ─────┐
rkllama       ─┤           │  LiteLLM (hidden)  │
llama.cpp     ─┤──────────▶│  Per-agent keys    │────────────▶ OPENAI_API_KEY + OPENAI_BASE_URL
vLLM / exo    ─┤           │  Auto-configured   │
Cloud APIs    ─┘           └────────────────────┘

Secrets Mgr   ────────────▶ Env var injection ──────────────▶ Agents get secrets as env vars
```

## 1. Channel Hub

TinyAgentOS manages all external messaging connections. Frameworks never touch Telegram/Discord APIs directly.

### Components

**Platform Connectors** — one per platform:
- Telegram: long-polling or webhook, owns bot token
- Discord: WebSocket gateway, owns bot token
- Slack: Events API + Web API, owns OAuth token
- Email: IMAP polling + SMTP send
- Web Chat: built-in WebSocket in TinyAgentOS UI
- Webhook: generic HTTP incoming/outgoing

Each connector:
- Owns the connection credentials (from Secrets Manager)
- Receives incoming messages → converts to universal format
- Sends outgoing messages → translates from universal to platform-native

**Message Router** — routes universal messages to the correct agent based on:
- Per-agent bot mode: each agent has own bot token, messages go directly to that agent
- Shared bot mode: one bot, routing by command (`/naira ...`) or chat assignment
- Per-agent is the default/primary mode

**Adapter Manager** — runs one thin adapter process per active agent:
- Receives universal messages from router via HTTP POST to `localhost:{agent_port}/message`
- Calls the framework's generate function
- Returns universal response
- Auto-generated based on framework type (~20 lines per framework)
- Adapter templates stored in `tinyagentos/adapters/`

**Response Translator** — converts universal responses to platform-specific:

| Universal | Telegram | Discord | Slack | Email |
|-----------|----------|---------|-------|-------|
| markdown text | MarkdownV2 | Embed description | mrkdwn | HTML |
| `buttons[]` | InlineKeyboard | Components/Buttons | Block Kit buttons | Links |
| `images[]` | sendPhoto | Embed image | File upload | Inline/attached |
| `code` blocks | `<pre>` | ``` blocks | ``` blocks | `<pre>` |
| `cards[]` | Multiple messages | Multiple embeds | Block Kit sections | HTML cards |

### Universal Message Format

**Incoming (to agent):**
```json
{
  "id": "msg-uuid",
  "from": {"id": "user:12345", "name": "Jay", "platform": "telegram"},
  "channel": {"id": "chat:67890", "type": "telegram", "name": "DM with Jay"},
  "text": "analyse my revenue",
  "attachments": [],
  "reply_to": null,
  "timestamp": 1775500000
}
```

**Outgoing (from agent) — structured format:**
```json
{
  "content": "Revenue is up **15%** this quarter.\n\n```python\ndf.groupby('month').sum()\n```",
  "buttons": [
    {"label": "Show Details", "action": "revenue_details"},
    {"label": "Export CSV", "action": "export_csv"}
  ],
  "images": ["/data/images/revenue-chart.png"],
  "cards": [],
  "reply_to": "msg-uuid"
}
```

**Outgoing — inline hint fallback (any framework):**
```
Revenue is up **15%** this quarter.

[button:Show Details:revenue_details]
[button:Export CSV:export_csv]
[image:/data/images/revenue-chart.png]
```

TinyAgentOS parses both formats. Frameworks that return plain text with inline hints get rich features for free.

**Passthrough mode:** For frameworks like OpenClaw that have native platform support, the adapter can return raw platform payloads:
```json
{
  "passthrough": true,
  "platform": "telegram",
  "payload": {"method": "sendMessage", "chat_id": 12345, "text": "...", "reply_markup": {...}}
}
```

### Bot Modes

**Per-agent (default):** Each agent has its own bot identity.
- naira has her own Telegram bot (@NairaBot)
- stanley has his own (@StanleyBot)
- Users message the agent directly
- Most natural UX — feels like messaging a person

**Shared:** One bot, TinyAgentOS routes.
- Single bot token for all agents
- Route by command: `/naira analyse revenue`
- Or assign Telegram chats to agents in config
- Simpler setup for quick start

### Message Flow

```
User sends "analyse my revenue" to @NairaBot on Telegram
  → Telegram Connector receives update via long-polling
  → Converts to universal format: {from: "user:12345", text: "analyse my revenue"}
  → Router: this bot token belongs to agent "naira"
  → POST http://localhost:9001/message → naira's SmolAgents adapter
  → Adapter calls SmolAgents code_agent.run("analyse my revenue")
  → Agent uses OPENAI_API_KEY (LiteLLM) for inference
  → Agent uses QMD_SERVER for memory retrieval
  → Returns: {content: "Revenue up **15%**", images: ["chart.png"], buttons: [{label: "Details"}]}
  → Response Translator: markdown→MarkdownV2, buttons→InlineKeyboard, image→sendPhoto
  → Telegram Connector: sends 3 API calls (photo, message with keyboard)
  → User sees rich formatted response with chart and buttons
```

## 2. LLM Proxy Layer

LiteLLM runs as a hidden internal service on `localhost:4000`. Users never see its dashboard.

### Auto-Configuration

TinyAgentOS generates LiteLLM config from its own backend config on startup:

```python
# TinyAgentOS reads its config:
backends = [
    {"name": "fedora-gpu", "type": "ollama", "url": "http://fedora:11434", "priority": 1},
    {"name": "local-npu", "type": "rkllama", "url": "http://localhost:7833", "priority": 3},
]

# Generates LiteLLM config:
litellm_config = {
    "model_list": [
        {"model_name": "default", "litellm_params": {"model": "ollama/qwen3-8b", "api_base": "http://fedora:11434"}, "metadata": {"priority": 1}},
        {"model_name": "default", "litellm_params": {"model": "ollama/qwen3-8b", "api_base": "http://localhost:7833"}, "metadata": {"priority": 3}},
    ],
    "router_settings": {"routing_strategy": "simple-shuffle", "num_retries": 2, "fallbacks": [...]},
}
```

### Per-Agent Virtual Keys

When an agent is deployed:
1. TinyAgentOS calls `POST http://localhost:4000/key/generate` with agent-specific settings
2. LiteLLM returns a virtual key: `sk-taos-naira-a1b2c3d4`
3. Key has: allowed models, budget limit, rate limit (RPM/TPM)
4. TinyAgentOS injects as `OPENAI_API_KEY` in agent's environment
5. Agent framework just uses standard OpenAI SDK — works with every framework

### Provider Management (UI)

In Settings page:

**Provider list:**
- Each provider shows: name, type, URL, status (green/red), latency, model count
- "Test Connection" button: hits health + models endpoints, shows results before saving
- Only saves after successful test (or explicit override)
- Add/edit/delete providers

**Per-agent model assignment (in Agent config):**
- Model dropdown (populated from LiteLLM's discovered models)
- Fallback chain builder: drag to reorder priority
- "Test Agent Config" button: sends test prompt, shows response + which provider handled it

**Usage monitoring (in Agent Workspace):**
- Tokens used (input/output) per day/week/month
- Cost tracking (paid APIs)
- Average latency, error rate
- Provider distribution pie chart

### Fallback & Load Balancing

LiteLLM handles natively:
- Priority-based routing (matches TinyAgentOS backend priorities)
- Automatic failover on provider error
- Rate limiting per key
- Cost tracking per key

## 3. Secrets & Config Injection

When TinyAgentOS deploys or starts an agent, it injects everything via environment variables:

```bash
# LLM access (via LiteLLM proxy)
OPENAI_API_KEY=sk-taos-naira-a1b2c3d4
OPENAI_BASE_URL=http://localhost:4000

# Agent identity
TAOS_AGENT_NAME=naira
TAOS_AGENT_ID=agent-naira
TAOS_WEBHOOK_PORT=9001

# Memory
QMD_SERVER=http://localhost:7832

# Secrets (only ones this agent has access to, from Secrets Manager)
DISCORD_BOT_TOKEN=xxx
TELEGRAM_BOT_TOKEN=xxx
# ... any user-defined secrets assigned to this agent

# TinyAgentOS callback API
TAOS_API_URL=http://localhost:6969
TAOS_API_KEY=internal-agent-key
```

### Hot-Swap Scenario

User changes naira from SmolAgents to PicoClaw in the dashboard:
1. TinyAgentOS stops naira's SmolAgents adapter process
2. Starts PicoClaw adapter with the SAME env vars
3. Channel Hub keeps routing Telegram messages to naira's webhook port (unchanged)
4. LiteLLM key stays the same (unchanged)
5. QMD memory stays the same (unchanged)
6. Secrets stay the same (unchanged)
7. Zero reconfiguration — everything just works

## 4. Framework Adapters

Each adapter is a thin HTTP server (~20-50 lines) that bridges TinyAgentOS's universal message format to the framework's API.

### Adapter Template

```python
# adapters/smolagents_adapter.py
from fastapi import FastAPI
import os

app = FastAPI()
agent = None  # lazy init

@app.post("/message")
async def handle_message(msg: dict):
    global agent
    if agent is None:
        from smolagents import CodeAgent
        agent = CodeAgent(model_id="default")  # uses OPENAI_API_KEY + OPENAI_BASE_URL
    
    result = agent.run(msg["text"])
    return {"content": str(result)}

@app.get("/health")
async def health():
    return {"status": "ok", "framework": "smolagents"}
```

### Adapter for OpenClaw (passthrough mode)

```python
# adapters/openclaw_adapter.py
# OpenClaw has its own channel system — adapter forwards to its gateway
@app.post("/message")
async def handle_message(msg: dict):
    # Forward to OpenClaw gateway
    resp = await httpx.post(f"http://localhost:18789/message", json=msg)
    return resp.json()  # may include passthrough platform payloads
```

### Adapter Registry

```python
ADAPTERS = {
    "smolagents": "adapters/smolagents_adapter.py",
    "pocketflow": "adapters/pocketflow_adapter.py",
    "picoclaw": "adapters/picoclaw_adapter.py",
    "openclaw": "adapters/openclaw_adapter.py",
    "swarm": "adapters/swarm_adapter.py",
    "langroid": "adapters/langroid_adapter.py",
    "generic": "adapters/generic_adapter.py",  # fallback for unknown frameworks
}
```

Each adapter is auto-started by the Adapter Manager when an agent is deployed or started. Port assigned dynamically from a pool (9001-9099).

## 5. Implementation Priority

1. **LiteLLM integration** — add as a service, auto-configure from backend config, per-agent keys. Quickest to implement and immediately useful.
2. **Adapter system** — adapter templates for top 5 frameworks, adapter manager to start/stop them.
3. **Channel Hub core** — Telegram connector first (most common), universal message format, router.
4. **Response translator** — rich format translation for each platform.
5. **Provider management UI** — test connection, per-agent model assignment, usage monitoring.
6. **Additional connectors** — Discord, Slack, email, web chat.

## 6. Non-Goals (This Spec)

- Building a full messaging platform (we route, not store — inter-agent messages use the existing AgentMessageStore)
- Replacing frameworks' internal logic — we only abstract external interfaces
- Supporting every Telegram/Discord feature on day one — start with text, images, buttons. Add more over time.
