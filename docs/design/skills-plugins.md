# Skills & Plugins — Framework-Agnostic Agent Capabilities

**Date:** 2026-04-09
**Status:** Draft
**Amended:** 2026-04-11 — skill availability follows **backend-driven
discovery**: a skill is "available" to an agent if some live backend
currently advertises the capabilities the skill needs (e.g. an
`image_generation` skill needs a backend advertising `image-generation`).
Registered skill definitions in the plugin catalog describe capability
requirements; the live scheduler answers "can it run right now?" at
invocation time. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

## Overview

A platform-level registry that gives agents capabilities (tools, skills, plugins) independent of their framework. TinyAgentOS owns the skill definitions and the adapters translate them into whatever the framework expects — MCP tools, Python functions, API calls, or config entries. Switch an agent from SmolAgents to OpenClaw and it keeps every skill assignment. Add a new plugin once and every compatible agent can use it.

This follows the same abstraction principle as Channel Hub (messaging) and LLM Proxy (inference): the platform owns the integration, the framework is a replaceable engine.

```
Platform Skills                 Adapter Translation              Framework Runtime
──────────────                  ───────────────────              ─────────────────
web_search        ─┐            ┌─── Skill Injector ───┐        ┌─ SmolAgents Tool
file_read         ─┤            │  Reads agent's skill  │        ├─ OpenClaw Action
code_exec         ─┤───────────▶│  assignments          │───────▶├─ PocketFlow Node
memory_search     ─┤            │  Translates per       │        ├─ LangChain Tool
image_generation  ─┤            │  framework             │        ├─ Hermes Function
browser_control   ─┘            └───────────────────────┘        └─ Generic MCP
```

## Skill Registry

### Skill Manifest

Each skill is defined once in the app catalog as a YAML manifest:

```
app-catalog/
  plugins/
    web-search/
      manifest.yaml
      tool.py            # implementation (optional — some skills are config-only)
    file-read/
      manifest.yaml
      tool.py
    code-exec/
      manifest.yaml
      tool.py
    memory-search/
      manifest.yaml
      tool.py
    image-generation-tool/
      manifest.yaml        # (already exists)
    playwriter/
      manifest.yaml        # (already exists)
    ...
```

### Manifest Format

```yaml
id: web-search
name: Web Search
type: plugin
version: 1.0.0
category: search                    # search, files, code, memory, media, browser, data, comms
description: "Search the web via SearXNG or Perplexica and return results"

requires:
  ram_mb: 0
  services: [searxng]               # optional — platform services this skill depends on

install:
  method: builtin                    # builtin | pip | npm | script | docker
  module: tinyagentos.tools.web_search

# MCP tool schema — the universal definition
tool_schema:
  name: web_search
  description: "Search the web and return relevant results"
  input_schema:
    type: object
    properties:
      query:
        type: string
        description: "Search query"
      max_results:
        type: integer
        default: 5
    required: [query]

# Which frameworks can use this skill
frameworks:
  smolagents: native                # has built-in web_search tool
  openclaw: native                  # has built-in web search
  pocketflow: adapter               # needs adapter translation
  agent-zero: native                # has built-in web browsing
  hermes: adapter                   # function calling, needs schema injection
  langroid: adapter                 # tool registration
  openai-agents-sdk: adapter        # function tool
  nanoclaw: adapter
  picoclaw: adapter
  microclaw: adapter
  ironclaw: adapter
  zeroclaw: unsupported             # too minimal
  nullclaw: unsupported             # no-op framework
  shibaclaw: adapter
  moltis: adapter

# Hardware requirements for the skill itself (not the framework)
hardware_tiers:
  arm-npu-16gb: full
  cpu-only: full
```

### Framework Compatibility Levels

| Level | Meaning | What happens |
|---|---|---|
| `native` | Framework has a built-in implementation of this capability | Skill Injector enables the framework's own version via config/flag |
| `adapter` | Framework supports custom tools but doesn't ship this one | Skill Injector registers the platform's tool implementation into the framework |
| `unsupported` | Framework can't use external tools at all | Skill greyed out in UI: "Requires: SmolAgents, OpenClaw, ..." |

### Skill Categories

| Category | Skills | Description |
|---|---|---|
| `search` | web_search, memory_search | Find information from web or agent memory |
| `files` | file_read, file_write, file_list | Read/write/list files in agent workspace |
| `code` | code_exec, code_review | Execute or analyse code |
| `media` | image_generation, audio_tts, audio_stt | Generate or process media |
| `browser` | browser_control, screenshot | Control a browser via Playwright |
| `data` | csv_parse, json_transform, pdf_extract | Parse and transform data formats |
| `comms` | send_email, send_message, create_canvas | Communicate via channels or canvas |
| `system` | shell_exec, http_request | Low-level system access (restricted) |

## Default Skills

These are the baseline skills available to every new agent. Derived from auditing the built-in tools across all 15 supported frameworks.

### Tier 1 — Included by Default

| Skill | Source | Notes |
|---|---|---|
| `memory_search` | Platform (QMD) | Every agent has a QMD instance. Always available. |
| `file_read` | Platform | Read files from agent workspace |
| `file_write` | Platform | Write files to agent workspace |

### Tier 2 — Enabled When Service Available

| Skill | Requires | Notes |
|---|---|---|
| `web_search` | SearXNG or Perplexica installed | Most frameworks ship a web search tool |
| `code_exec` | Sandbox configured | Python/JS execution in agent container |
| `image_generation` | SD backend available | Already implemented as MCP tool |
| `browser_control` | Playwriter installed | Already exists as plugin |

### Tier 3 — Optional / Install from Store

| Skill | Install | Notes |
|---|---|---|
| `audio_tts` | Kokoro/Piper/Chatterbox | Text to speech |
| `audio_stt` | Whisper | Speech to text |
| `send_email` | Docker Mailserver | Send email via agent |
| `pdf_extract` | Built-in | Parse PDFs into text |
| `http_request` | Built-in | Generic HTTP calls (restricted) |

## Skill Injector

The Skill Injector runs at agent startup, between config injection and adapter launch. It reads the agent's skill assignments and translates them into framework-specific configuration.

### Injection Flow

```
Agent Deploy/Start
  → Config Injection (env vars, secrets, LLM proxy key)
  → Skill Injector
      1. Read agent's assigned skills from AgentStore
      2. For each skill:
         a. Check framework compatibility (native/adapter/unsupported)
         b. Check hardware + service requirements
         c. If native: set framework config flag to enable it
         d. If adapter: register platform tool implementation
      3. Build framework-specific tool config
  → Adapter Launch (framework starts with tools configured)
```

### Framework Translation

Each framework adapter gains a `configure_tools()` method that accepts the universal skill list and returns framework-specific config.

**SmolAgents:**
```python
# Native tools enabled via import, adapter tools via Tool wrapper
from smolagents import CodeAgent, Tool

tools = []
for skill in assigned_skills:
    if skill.framework_level == "native":
        # SmolAgents built-in
        tools.append(load_smolagents_tool(skill.id))
    elif skill.framework_level == "adapter":
        # Wrap platform implementation as SmolAgents Tool
        tools.append(Tool.from_function(
            fn=skill.execute,
            name=skill.tool_schema["name"],
            description=skill.tool_schema["description"],
        ))

agent = CodeAgent(tools=tools, model=model)
```

**OpenClaw:**
```python
# OpenClaw uses MCP tool format natively
mcp_tools = []
for skill in assigned_skills:
    mcp_tools.append({
        "name": skill.tool_schema["name"],
        "description": skill.tool_schema["description"],
        "input_schema": skill.tool_schema["input_schema"],
    })
# Injected via config or API call to OpenClaw gateway
```

**PocketFlow:**
```python
# PocketFlow uses graph nodes — skills become callable nodes
for skill in assigned_skills:
    register_tool_node(
        name=skill.tool_schema["name"],
        handler=skill.execute,
        schema=skill.tool_schema["input_schema"],
    )
```

**Generic / MCP-compatible:**
```python
# For any framework that supports MCP, just pass the tool schema directly
# The platform's MCP server exposes all assigned skills
# Agent connects to localhost:{mcp_port} and discovers tools
```

### MCP Server Mode

For frameworks with native MCP support, the Skill Injector can run a per-agent MCP server instead of injecting tools individually:

```
Agent Container
├── Framework adapter (port 9001)
├── QMD serve (port 7832)
└── Skill MCP server (port 7900)     ← exposes all assigned skills as MCP tools
    ├── web_search
    ├── file_read
    ├── image_generation
    └── ...
```

The framework connects to `localhost:7900` and discovers available tools via MCP protocol. This is the cleanest integration path for MCP-native frameworks and new frameworks added in the future.

Environment variable injected:
```bash
TAOS_SKILLS_MCP_URL=http://localhost:7900
```

## Skill Store

### Schema

```sql
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT DEFAULT '',
    version TEXT NOT NULL,
    tool_schema TEXT NOT NULL DEFAULT '{}',     -- JSON: MCP tool definition
    frameworks TEXT NOT NULL DEFAULT '{}',       -- JSON: {framework_id: "native"|"adapter"|"unsupported"}
    requires_services TEXT DEFAULT '[]',         -- JSON: service dependencies
    requires_hardware TEXT DEFAULT '{}',         -- JSON: hardware tier requirements
    install_method TEXT NOT NULL DEFAULT 'builtin',
    install_target TEXT DEFAULT '',              -- module path, package name, or script
    installed INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);

CREATE TABLE agent_skills (
    agent_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    config TEXT DEFAULT '{}',                   -- JSON: per-agent skill config overrides
    PRIMARY KEY (agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);
```

### SkillStore Methods

```python
class SkillStore(BaseStore):
    # Registry
    register_skill(manifest: dict) -> None
    get_skill(skill_id: str) -> dict | None
    list_skills(category: str = None, installed: bool = None) -> list[dict]
    update_skill(skill_id: str, **fields) -> None
    remove_skill(skill_id: str) -> bool

    # Agent assignments
    assign_skill(agent_id: str, skill_id: str, config: dict = None) -> None
    unassign_skill(agent_id: str, skill_id: str) -> None
    get_agent_skills(agent_id: str) -> list[dict]
    is_compatible(skill_id: str, framework: str) -> str  # "native"|"adapter"|"unsupported"

    # Bulk
    get_available_skills(agent_id: str) -> list[dict]  # skills compatible with agent's framework
    sync_from_catalog() -> int  # scan app-catalog/plugins/, return count registered
```

## UI Integration

### Agent Settings — Skills Tab

When deploying or editing an agent, a "Skills" tab shows:

```
Skills                                              [+ Install More]
───────────────────────────────────────────────────────────────────

ENABLED
  [x] Memory Search          Search agent's knowledge base       [native]
  [x] File Read              Read files from workspace            [native]
  [x] File Write             Write files to workspace             [adapter]

AVAILABLE
  [ ] Web Search             Search the web via SearXNG           [adapter]
  [ ] Code Execution         Run Python/JS code                   [native]
  [ ] Image Generation       Generate images via Stable Diffusion [adapter]
  [ ] Browser Control        Control Chrome via Playwright        [adapter]  [Install Playwriter]

INCOMPATIBLE (requires framework with tool support)
  --- Shell Execute          Not supported by ZeroClaw
  --- HTTP Request           Not supported by ZeroClaw
```

- Toggle skills on/off per agent
- `[native]` / `[adapter]` badge shows how the framework uses this skill
- Skills requiring uninstalled services show an install button
- Incompatible skills are listed at the bottom, greyed out, with reason

### Store — Plugins Section

The existing app store already has a plugins section. Skills/plugins installed from the store automatically register in the skill registry. The manifest format above is backwards-compatible with the existing plugin manifests (image-generation-tool, playwriter) — they just gain optional `tool_schema` and `frameworks` fields.

## Routes

```
GET  /api/skills                         — list all registered skills
GET  /api/skills/{id}                    — get skill details
POST /api/skills/sync                    — sync skills from app catalog
GET  /api/agents/{id}/skills             — list agent's skill assignments
POST /api/agents/{id}/skills             — assign skill to agent
DELETE /api/agents/{id}/skills/{skill_id} — unassign skill
PUT  /api/agents/{id}/skills/{skill_id}  — update skill config for agent
GET  /api/skills/{id}/compatible         — list frameworks compatible with this skill
```

## Adapter Changes

Each adapter gains two new capabilities:

1. **Tool registration endpoint** — `POST /tools` accepts a list of tool schemas to register at runtime
2. **Tool execution callback** — when the framework calls a platform-provided tool, the adapter calls back to the Skill MCP server or directly to the tool's execute function

Updated adapter template:

```python
@app.post("/tools")
async def register_tools(tools: list[dict]):
    """Receive tool schemas from Skill Injector and register with framework."""
    for tool in tools:
        framework_register_tool(tool)  # framework-specific
    return {"registered": len(tools)}

@app.post("/message")
async def handle_message(msg: dict):
    # Framework now has access to all registered tools
    result = agent.run(msg.get("text", ""))
    return {"content": str(result)}
```

## Startup Sequence

```
1. Agent deploy triggered
2. Container created/started (LXC or Docker)
3. QMD serve started in container
4. Skill Injector reads agent's skill assignments
5. Skill Injector checks service/hardware requirements
6. Skill MCP server started with available skills (port 7900)
7. Config injected: env vars + TAOS_SKILLS_MCP_URL
8. Framework adapter started
9. Adapter calls POST /tools or connects to MCP server
10. Agent ready — has all assigned skills available
```

## Migration Path

Existing agents deployed before this feature get the Tier 1 default skills (memory_search, file_read, file_write) auto-assigned on upgrade. The existing `tools` multiselect in smolagents/openai-agents-sdk manifests maps directly to skill IDs — these become the initial skill assignments during migration.

## Non-Goals (This Spec)

- Custom skill authoring UI (future — users define their own MCP tools via the dashboard)
- Skill marketplace / sharing between TinyAgentOS instances
- Skill versioning with rollback
- Per-skill usage metering
- Skill chaining / composition (one skill calling another)
