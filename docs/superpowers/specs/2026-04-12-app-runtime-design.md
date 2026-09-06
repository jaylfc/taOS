# taOS App Runtime

## Overview

A proper app packaging, installation, and runtime system for taOS. Apps are independently packaged as `.taosapp` archives containing frontend bundles, backend modules, and agent tool declarations. Users install apps from the Store, and agents discover app capabilities automatically. The system supports three app types — native (in-process), container (Docker/LXC), and web (iframe) — all using the same package format.

Every taOS app is both a human tool and an agent tool. The agent manifest is a first-class part of every app package, enabling agents to discover and use any installed app's capabilities.

---

## Package Format

A `.taosapp` file is a zip archive with a standard structure:

```
my-app.taosapp
├── manifest.yaml        # App metadata, permissions, type
├── agent.yaml           # Agent tool declarations (MCP-style)
├── icon.png             # App icon (256x256 recommended)
├── bundle.js            # Frontend React component (native apps)
├── bundle.css           # Frontend styles (optional)
├── backend/             # Python backend module (optional)
│   ├── __init__.py
│   ├── routes.py        # FastAPI router
│   └── ...
└── container.yaml       # Docker/LXC config (container apps only)
```

### manifest.yaml

```yaml
id: reddit-client
name: Reddit
version: 1.0.0
author: jaylfc
description: Browse Reddit, save threads to Knowledge Base, monitor for changes
icon: icon.png
app_type: native           # native | container | web

# For native apps
entry_component: RedditClientApp    # Exported React component name
default_size: { w: 1000, h: 650 }
min_size: { w: 550, h: 400 }
singleton: true

# For container apps (ignored for native/web)
container:
  image: ghcr.io/jaylfc/taos-reddit:latest
  ports: [8080]
  volumes: [data:/app/data]

# For web apps (ignored for native/container)
web:
  url: https://example.com/app
  sandbox: allow-scripts allow-same-origin

# Permissions this app needs
permissions:
  - knowledge:read         # Can read from Knowledge Base
  - knowledge:write        # Can write to Knowledge Base (ingest)
  - knowledge:monitor      # Can create monitoring tasks
  - secrets:read           # Can read secrets (for OAuth tokens)
  - agents:read            # Can read agent list
  - network:external       # Can make external HTTP requests

# App store metadata
category: platform
tags: [reddit, social, knowledge-capture]
source_url: https://github.com/jaylfc/tinyagentos
pricing: free                # free | paid (future)
```

### agent.yaml

MCP-style tool declarations. Agents discover these automatically when the app is installed.

```yaml
tools:
  - name: search_reddit
    description: Search Reddit for threads matching a query
    input:
      type: object
      properties:
        query: { type: string, description: Search query }
        subreddit: { type: string, description: Limit to a specific subreddit }
      required: [query]
    route: GET /api/reddit/search

  - name: save_reddit_thread
    description: Save a Reddit thread to the Knowledge Base for indexing and monitoring
    input:
      type: object
      properties:
        url: { type: string, description: Reddit thread URL }
      required: [url]
    route: POST /api/knowledge/ingest

  - name: fetch_reddit_thread
    description: Fetch a Reddit thread with all comments
    input:
      type: object
      properties:
        url: { type: string, description: Reddit thread URL }
      required: [url]
    route: GET /api/reddit/thread

  - name: browse_subreddit
    description: Browse recent posts in a subreddit
    input:
      type: object
      properties:
        name: { type: string, description: Subreddit name without r/ prefix }
        sort: { type: string, enum: [hot, new, top], description: Sort order }
      required: [name]
    route: GET /api/reddit/subreddit

data_sources:
  - name: reddit_threads
    description: All Reddit threads saved to the Knowledge Base
    query: GET /api/knowledge/items?source_type=reddit

notifications:
  - name: thread_updated
    description: Fired when a monitored Reddit thread has new comments or changes
    channel: knowledge-updates
```

---

## App Types

### Native Apps

Frontend JS bundle + optional Python backend. Run in the main taOS process.

**Frontend loading:**
- Bundle served from `GET /api/apps/{app-id}/bundle.js`
- Loaded via dynamic `import()` when the user opens the app
- The bundle exports a React component matching `entry_component` from manifest
- Component receives `{ windowId: string }` props (same as current apps)

**Backend loading:**
- Python module at `data/apps/{app-id}/backend/`
- Contains a FastAPI `router` in `routes.py`
- On install, the runtime imports the router and includes it in the main FastAPI app under `/api/apps/{app-id}/*`
- On uninstall, the router is removed

**Permissions:** Declared in manifest. The runtime enforces permissions by wrapping the app's backend routes with middleware that checks whether the app has the required permission for each API call it makes to core taOS services.

### Container Apps

Docker or LXC containers. Full filesystem and process isolation.

**Lifecycle:** On install, pull the container image. On start, run the container with the declared ports and volumes. On stop, stop the container (data persists in volumes). On uninstall, stop + remove container + optionally remove volumes.

**Communication:** The container exposes HTTP endpoints. taOS proxies requests from the frontend to the container's ports. Agent tools declared in `agent.yaml` point to these proxied endpoints.

**UI:** Container apps either serve their own web UI (proxied via iframe in a taOS window) or use noVNC for graphical Linux apps.

### Web Apps

Third-party web UIs embedded in a taOS window via iframe.

**Sandboxing:** The iframe uses the `sandbox` attribute from the manifest. A postMessage bridge allows limited communication with taOS (e.g. requesting auth tokens, triggering notifications).

**Agent tools:** Web apps can still declare agent tools in `agent.yaml` if they expose an API.

---

## Installation Flow

### Install

1. User clicks "Install" in Store on an app listing
2. Store sends `POST /api/apps/install` with `{ source_url, app_id }`
3. Backend downloads the `.taosapp` file from the source URL
4. Backend validates the archive: manifest.yaml must exist, signature check (future)
5. Backend extracts to `data/apps/{app-id}/`
6. Backend registers in `installed_apps` SQLite table: id, name, version, app_type, installed_at, enabled
7. If native app with backend: dynamically import and register the FastAPI router
8. If container app: pull the Docker image (background task)
9. Backend registers agent tools from `agent.yaml` into the agent tool registry
10. Backend returns success
11. Frontend reloads app registry from `GET /api/apps/installed`
12. App appears in Launchpad

### Uninstall

1. User clicks "Uninstall" in Store or app context menu
2. `DELETE /api/apps/{app-id}`
3. If running (container): stop the container
4. Deregister backend routes
5. Deregister agent tools
6. Remove from `installed_apps` table
7. Delete `data/apps/{app-id}/` directory
8. Frontend reloads registry, app disappears from Launchpad

### Update

1. Store checks source URL for newer version (comparing manifest version)
2. Downloads new `.taosapp`, extracts alongside old version
3. Swaps: deregister old routes/tools, register new ones
4. Deletes old version files

---

## App Registry API

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/apps/installed` | List installed apps (id, name, icon, app_type, version, enabled) |
| GET | `/api/apps/{id}` | Get single app details including manifest |
| POST | `/api/apps/install` | Install from source URL |
| DELETE | `/api/apps/{id}` | Uninstall |
| POST | `/api/apps/{id}/enable` | Enable a disabled app |
| POST | `/api/apps/{id}/disable` | Disable without uninstalling |
| GET | `/api/apps/{id}/bundle.js` | Serve frontend JS bundle (native apps) |
| GET | `/api/apps/{id}/bundle.css` | Serve frontend CSS (optional) |
| GET | `/api/apps/{id}/icon.png` | Serve app icon |
| GET | `/api/apps/tools` | List all agent tools from all installed apps |

### Frontend Registry

The frontend `app-registry.ts` is replaced by a dynamic registry:

```typescript
// Core apps — always available, hardcoded
const CORE_APPS: AppManifest[] = [
  { id: "messages", name: "Messages", ... },
  { id: "agents", name: "Agents", ... },
  // ... all current core apps
];

// Installed apps — loaded from backend on startup
async function loadInstalledApps(): Promise<AppManifest[]> {
  const res = await fetch("/api/apps/installed");
  const data = await res.json();
  return data.apps.map(appToManifest);
}

// Merged registry
function getAllApps(): AppManifest[] {
  return [...CORE_APPS, ...installedApps];
}
```

For installed native apps, the `component` field uses a dynamic import:
```typescript
component: () => import(`/api/apps/${id}/bundle.js`).then(m => ({ default: m[entryComponent] }))
```

---

## Agent Tool Registry

A unified registry of all tools available to agents, populated from installed apps' `agent.yaml` files.

```python
class AgentToolRegistry:
    async def get_all_tools() -> list[AgentTool]
    async def get_tools_for_app(app_id: str) -> list[AgentTool]
    async def invoke_tool(tool_name: str, input: dict) -> dict
    async def register_app_tools(app_id: str, tools: list[dict])
    async def deregister_app_tools(app_id: str)
```

When an agent processes a message, it receives the full tool list from all installed apps. The agent can call any tool, and the runtime routes the call to the correct app's backend endpoint.

Core taOS services (Knowledge Base, Memory, Channels, etc.) also register their tools in the same registry, so agents see everything in one unified tool list.

---

## Core vs Installable Apps

### Core (cannot uninstall, shipped built-in)

Messages, Agents, Files, Store, Settings, Library, Memory, Channels, Secrets, Routines, Dashboard, Models, Providers, Cluster, Import, Images, Calculator, Calendar, Contacts, Browser, Media Player, Text Editor, Image Viewer, Terminal, Chess, Wordle, Crosswords

### Installable (available in Store)

Reddit Client, YouTube Library, GitHub Browser, X Monitor, Agent Browsers — and any future community/third-party apps.

These apps currently live in the main codebase but will be extracted into `.taosapp` packages. The extraction is a migration task — move their frontend bundles and backend modules into the package format, update the Store catalog to reference them as downloadable packages.

---

## Package Sources (v1)

The built-in catalog (`app-catalog/`) contains metadata for all known apps. Each entry includes a `source_url` pointing to where the `.taosapp` can be downloaded:

- **GitHub Releases** — `https://github.com/jaylfc/taos-app-reddit/releases/latest/download/reddit-client.taosapp`
- **Docker Hub** — for container apps, the manifest references a Docker image tag
- **Self-hosted** — developers host their own `.taosapp` files at any URL
- **Future: taOS Cloud Store** — `https://store.tinyagentos.com/packages/{app-id}/{version}.taosapp`

Users can also install from a direct URL: paste a `.taosapp` URL into the Store, it downloads and installs.

### Store Sections

The Store UI has two app sections:

- **by taOS** — first-party apps built and maintained by the taOS team. Reddit Client, YouTube Library, GitHub Browser, X Monitor, Agent Browsers, and future official apps. Curated, tested, guaranteed compatibility.
- **for taOS** — community and third-party apps. Built by developers using the taOS SDK. Published by anyone, discovered via the catalog or direct URL. Quality varies — future: ratings, reviews, verified badges.

---

## Permissions System

Apps declare permissions in manifest.yaml. The runtime enforces them.

| Permission | Grants |
|-----------|--------|
| `knowledge:read` | Read Knowledge Base items and search |
| `knowledge:write` | Ingest content into Knowledge Base |
| `knowledge:monitor` | Create monitoring tasks |
| `secrets:read` | Read secrets (OAuth tokens, API keys) |
| `secrets:write` | Store secrets |
| `agents:read` | List agents and their configs |
| `agents:write` | Create/modify agents |
| `network:external` | Make HTTP requests to external URLs |
| `files:read` | Read from shared folders |
| `files:write` | Write to shared folders |
| `containers:manage` | Create/destroy Docker containers |
| `system:info` | Read hardware info, system stats |

v1 enforcement: apps declare permissions but enforcement is advisory (logged, not blocked). Full enforcement in a future hardening pass.

---

## Migration Plan

The existing platform apps (Reddit, YouTube, GitHub, X, Browsers) need to be extracted from the main codebase into `.taosapp` packages:

1. Build the app runtime infrastructure (registry, installer, loader)
2. Create a build script that packages an existing app into `.taosapp` format
3. Extract each platform app one at a time
4. Update the Store catalog entries with `.taosapp` download URLs
5. Remove the extracted app code from the main bundle

Core apps remain in the main codebase permanently.

---

## Non-Goals (v1)

- Payment processing or monetisation features (pricing field exists in manifest, ignored for now)
- App signing or code review workflow (future security hardening)
- Automatic updates (manual install of new version for v1)
- App-to-app communication beyond agent tools
- Multi-version support (one version installed at a time)
- App sandboxing enforcement (permissions are advisory in v1)
