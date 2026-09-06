# MCP App — Design Spec

## Overview

A new platform app **MCP** that manages Model Context Protocol servers users have installed from the Store. Think "the Agents app, but for MCP servers" — with a permissions layer that controls which agents can call which tools on which resources.

## Problem

taOS ships 43+ MCP plugins in the Store. Users can install them but there's no UI to manage what's actually running, inspect logs, configure env vars, grant access to specific agents, or remove a server that's no longer needed. Today they'd have to edit YAML by hand, and there is no per-agent access control — once installed, any agent could theoretically call any tool.

## Design

### Install / uninstall split

- **Install** happens in the Store (unchanged). Installing an MCP server places its code on disk and registers it with the controller; it is NOT automatically attached to any agent.
- **Uninstall** can happen from either the Store or the MCP app. Both paths call the same uninstall flow (`DELETE /api/mcp/servers/{id}`), which:
  1. Stops the running process
  2. Drops all attachments and env secrets for this server
  3. Removes the code from disk
  4. Emits a notification listing which agents lost access

The MCP app is the management surface for already-installed servers — start/stop, configure, grant access, remove. It is NOT the install entry point.

### Permission model

Three scopes, combinable, default to zero-access:

| Scope | Granularity | What it controls |
|---|---|---|
| **Server** | `all` / `agent:<name>` / `group:<name>` | Which agents can use the server at all |
| **Tool** | Per tool within a server | Which tools are callable (allowlist) |
| **Resource** | Per file path, URL pattern, channel etc. | Which resources the tools may touch |

**Zero-default rule.** A fresh install has no attachments. The server is installed but unreachable to every agent until the user explicitly grants access. No silent "all agents" default — this avoids the surprise of a new filesystem server suddenly being callable by every agent.

**Combining scopes.** Attachments AND together:
- Server attachment is required: if no attachment grants this agent access, deny.
- If tool list is set, the tool must appear in it.
- If resource constraints are set, the resource must match.
- Empty tool/resource lists mean "no restriction within this scope" — server-level access is enough.

**Precedence.** When multiple attachments match (e.g. agent belongs to two groups both attached), permissions UNION. The broader of any two attachments wins. Denies are not expressible — absence of grant is the only deny.

### App registration

Registry entry:
- `id: "mcp"`
- `name: "MCP"`
- `icon: "plug"`
- `category: "platform"`
- `pinned: false`, `launchpadOrder: 9.5`

### Layout

Split-view via `MobileSplitView` — list on the left, detail on the right.

**List (grouped by status):**
- **Running** — actively connected, green dot
- **Stopped** — installed but not running, grey dot
- **Failed** — crashed / error, red dot
- **Installing / Updating** — spinner

Each row shows: icon, server name, transport badge (stdio / http / ws), small text for last activity, PID, memory.

**Detail (tabbed):**
- **Overview** — status pill + start/stop/restart, description, capability counts, **Uninstall** (destructive, confirm modal)
- **Permissions** — attachments list, "+Attach" opens a modal with scope picker (all / agent / group), per-tool checklist (defaults off), per-resource constraint fields
- **Env** — editable env vars, stored via SecretsStore
- **Config** — JSON editor for custom config
- **Logs** — live stdout/stderr tail, colour-coded errors, copyable, SSE transport
- **Used by** — live list of agents currently making calls, tap-through to the agent

### Uninstall flow

Uninstall is a destructive action with cascade. The confirm dialog must show:
- Server name + version
- Number of agent attachments that will be removed, with names
- Count of env secrets that will be deleted
- A typed confirmation ("Type the server name to confirm") for servers with 3+ active attachments

No soft-delete. The attachment rows and env secrets are permanently dropped.

### Install flow (reminder, not implemented here)

Install stays in the Store. From within the MCP app, the empty state shows:
- "No MCP servers installed yet"
- Large button "Browse MCP servers in Store" that opens the Store filtered to `type=mcp`

Inside a detail view, if the user wants to grant access to a new agent, a quick "Attach to new agent" button opens the Agents app at the deploy wizard with this MCP server pre-selected in the skills step.

### Backend

`tinyagentos/mcp/` (new package):
- `registry.py` — installed-server state in SQLite, catalog lookup
- `supervisor.py` — process lifecycle: spawn via stdio / SSE / WebSocket transport, tail logs to ring buffer, restart on crash (bounded), kill on uninstall
- `permissions.py` — single gate: `can_call(agent, server, tool, resource?) -> (allowed, reason)`
- `proxy.py` — when agent makes an MCP tool call, check permissions, route to supervisor, stream response
- `secrets.py` — env var handling via the existing SecretsStore (no new secret store)

`tinyagentos/routes/mcp.py`:
```
GET    /api/mcp/servers
POST   /api/mcp/servers/{id}/start
POST   /api/mcp/servers/{id}/stop
POST   /api/mcp/servers/{id}/restart
DELETE /api/mcp/servers/{id}                  # uninstall (cascade)
GET    /api/mcp/servers/{id}/logs?since=&limit=
GET    /api/mcp/servers/{id}/logs/stream      # SSE
GET    /api/mcp/servers/{id}/capabilities
GET    /api/mcp/servers/{id}/permissions
POST   /api/mcp/servers/{id}/permissions      # attach
DELETE /api/mcp/servers/{id}/permissions/{attachment_id}
GET    /api/mcp/servers/{id}/config
PUT    /api/mcp/servers/{id}/config
GET    /api/mcp/servers/{id}/env
PUT    /api/mcp/servers/{id}/env              # delegates to SecretsStore
GET    /api/mcp/servers/{id}/used-by          # live agent list
```

### Data model

```sql
CREATE TABLE mcp_servers (
    id TEXT PRIMARY KEY,            -- matches catalog id (e.g. "mcp-fetch")
    version TEXT NOT NULL,
    installed_at INTEGER NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',  -- JSON overrides
    transport TEXT NOT NULL,            -- stdio | sse | ws
    running INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    last_started_at INTEGER,
    last_exit_code INTEGER,
    last_error TEXT
);

CREATE TABLE mcp_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL,           -- 'all' | 'agent' | 'group'
    scope_id TEXT,                      -- agent name / group name / NULL for 'all'
    allowed_tools TEXT NOT NULL DEFAULT '[]',      -- JSON array
    allowed_resources TEXT NOT NULL DEFAULT '[]',  -- JSON array of pattern strings
    created_at INTEGER NOT NULL,
    UNIQUE(server_id, scope_kind, scope_id)
);

-- env vars live in the existing SecretsStore, namespaced `mcp:<server_id>`
```

### Mobile

Same split-view pattern as other refactored apps:
- List fills on mobile, tap slides to detail
- Tabs in detail are a horizontal scroll bar on mobile instead of a segmented control
- Permissions attach modal becomes a bottom sheet
- Logs open in a full-height sub-view

### Permissions hints on install

MCP servers can be powerful. The catalog manifest declares what the server wants to do (filesystem access, network, app control). When the user attaches a server to an agent in the MCP app, the attach modal shows a clear summary:

> "mcp-filesystem exposes 4 tools. Granting access to `weatherbot` will let it: **read** files under allowed paths, **write** files under allowed paths, **list** directories, **create** directories. It will NOT be able to: delete, move, or chmod (not in tool list)."

## Out of Scope

- Custom / uploaded MCP servers not from the catalog — later
- MCP sandbox isolation (container per server) — future; today they run in-process on the controller
- OAuth wizards for servers that need them (GitHub, Slack) — covered by SecretsStore but the UX wizard is follow-up

## Implementation Order

1. Update spec (this doc)
2. Backend: `MCPServerStore`, `Supervisor`, `Permissions`, uninstall cascade
3. Routes
4. Proxy wiring into the agent tool-call path
5. Frontend: list, tabs, permissions modal, uninstall confirm
6. E2E: install mcp-fetch via Store, attach to a test agent with URL-pattern resource constraints, verify allowed URL succeeds and blocked URL returns 403
