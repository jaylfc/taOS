# Agent Browsers

## Overview

A container management app for persistent Chromium browser instances in taOS. Each agent can have multiple named browser profiles with persistent data (passwords, bookmarks, cookies, extensions). Browser containers are ephemeral Docker instances backed by named volumes — stopping a browser doesn't erase its data. Cookie export enables automatic auth for platform apps (X Monitor, YouTube Library). Containers deployable to any cluster node.

Build order: #7 in the Knowledge Capture Pipeline. Unblocks X Monitor (cookie auth) and YouTube Library (cookie fallback).

---

## Architecture

```
desktop/src/
├── apps/AgentBrowsersApp.tsx       # Main app component
└── lib/agent-browsers.ts           # Typed fetch wrappers for /api/agent-browsers/*

tinyagentos/
├── agent_browsers.py               # AgentBrowsersManager: Docker lifecycle, volume management
└── routes/agent_browsers.py        # /api/agent-browsers/* endpoints
```

Registered in `app-registry.ts` as:
- `id: "agent-browsers"`
- `name: "Browsers"`
- `icon: "globe"`
- `category: "platform"`
- `launchpadOrder: 16`
- `singleton: true`
- `pinned: false`
- `defaultSize: { w: 1000, h: 650 }`
- `minSize: { w: 550, h: 400 }`

---

## Container Model

### Docker-Based

Each browser profile runs as a Docker container with a named persistent volume.

**Container:** Ephemeral Chromium instance. Image: `chromium-vnc` (custom or `kasmweb/chrome` or similar ARM64-compatible image). Destroyed when stopped. Recreated on start.

**Volume:** Named Docker volume `taos-browser-{agent}-{profile}`. Stores the Chromium user data directory (passwords, bookmarks, cookies, localStorage, extensions, history). Survives container removal.

**Lifecycle:**
- **Start:** `docker run -d --name taos-browser-{agent}-{profile} -v taos-browser-{agent}-{profile}:/home/user/.config/chromium --rm {image}`
- **Stop:** `docker stop taos-browser-{agent}-{profile}` — container destroyed, volume persists
- **Delete container:** Same as stop (containers are always ephemeral)
- **Delete data:** `docker volume rm taos-browser-{agent}-{profile}` — separate explicit action with confirmation

### Profiles

Each agent can have multiple named browser profiles (e.g. "work", "personal", "reddit"). Profiles are identified by `{agent_name}-{profile_name}`.

**Constraint:** One active container per profile at a time. Starting a second profile for the same agent stops the first (resource safety on the Pi — Chromium is ~200-400MB RAM per instance).

**Reassignment:** Profiles can be reassigned between agents. The volume stays the same, just the ownership metadata changes.

---

## Preview

### CDP Screenshots (default)

When a browser container is running, periodic screenshots via Chrome DevTools Protocol:
- Container exposes `--remote-debugging-port=9222`
- Backend fetches `http://container:9222/json` to get the active tab
- `Page.captureScreenshot` CDP command returns a PNG
- Cached with 30-second TTL
- Displayed as thumbnail in the app card grid

### Interactive noVNC (on demand)

Click "Connect" on a running browser to open an interactive session as a standard taOS window:
- Container runs a VNC server + noVNC websocket proxy
- taOS opens a new window (using the standard Window component) with the noVNC stream in an iframe
- The window appears in the dock like any other app: "Browser - {profile_name}"
- User can resize, minimize, maximize, and manage it like any other taOS window
- Full browser interaction — click, type, browse, log into sites
- Closing the window disconnects the VNC stream but the container keeps running
- Multiple browser windows can be open simultaneously (one per profile)

---

## Cookie Export

### Internal API

`GET /api/agent-browsers/{agent}/{profile}/cookies?domain={domain}`

Returns cookies for the specified domain from the browser profile's Chromium cookie database. The backend reads the SQLite cookie store at `{volume}/Default/Cookies` (Chromium's cookie DB path).

Platform apps call this automatically when they need auth:
- X Monitor: requests `domain=x.com`, looks for `auth_token` and `ct0`
- YouTube Library: requests `domain=youtube.com`, looks for `SID`, `HSID`
- Reddit Client: requests `domain=reddit.com`, looks for `reddit_session`

### Login Status Indicators

Each browser profile shows per-site login status:
- **Green dot:** Auth cookies detected for that site
- **Red dot:** No auth cookies / expired
- Sites checked: X (auth_token + ct0), GitHub (user_session), YouTube (SID), Reddit (reddit_session)

Detection runs on profile start and periodically while running.

---

## Cluster Placement

Browser containers are deployable to any cluster node:

- **Default:** Local (controller node)
- **Manual placement:** User picks a worker node from the Cluster app's node list
- **Auto-schedule:** (future) Scheduler places on the node with most available RAM
- **Migration:** Stop container on node A, start on node B with the same volume. Volume data must be accessible from both nodes (requires shared storage or volume sync — depends on storage pools #191)

The UI shows which node each browser is running on with a node badge.

---

## Layout

Two-panel: card grid on the left, detail panel on the right.

### Card Grid

Each browser profile as a card:
- Thumbnail (CDP screenshot or placeholder if stopped)
- Profile name + agent name
- Status badge: Running (green), Stopped (grey)
- Node location badge
- Login status dots per site
- Quick actions: Start/Stop toggle

### Detail Panel

When a profile is selected:
- Header: profile name, agent, node, status
- **Preview area:** CDP screenshot (updates every 30s) or noVNC iframe when connected
- **Login status:** List of sites with green/red dots
- **Actions:**
  - Start / Stop
  - Connect (launch noVNC interactive session)
  - Disconnect (stop VNC proxy)
  - Assign to agent (dropdown)
  - Move to node (dropdown of cluster nodes)
  - Delete container (stop if running)
  - Delete data (separate button, red, with confirmation: "This permanently removes all passwords, bookmarks, cookies, and browsing history for this profile.")

### Create Profile

"+ New Profile" button opens a form:
- Profile name (text input)
- Assign to agent (dropdown, optional — can be unassigned)
- Node (dropdown, default: local)

---

## Backend: AgentBrowsersManager

File: `tinyagentos/agent_browsers.py`

### State

SQLite table `agent_browsers` in `data/agent-browsers.db`:

```
agent_browsers:
  id: text (uuid)
  agent_name: text (nullable — can be unassigned)
  profile_name: text
  node: text (default "local")
  status: text (stopped, running, error)
  container_id: text (nullable — Docker container ID when running)
  created_at: real
  updated_at: real
```

### Methods

- `create_profile(profile_name, agent_name?, node?) -> Profile`
- `delete_profile(profile_id) -> bool` — stops container if running, does NOT delete volume
- `delete_profile_data(profile_id) -> bool` — deletes the Docker volume (confirmation must happen in the route layer)
- `start_browser(profile_id) -> str` — creates and starts Docker container, returns container_id. Stops other running containers for the same agent first.
- `stop_browser(profile_id) -> bool` — stops and removes the Docker container
- `get_screenshot(profile_id) -> bytes | None` — CDP screenshot, cached 30s
- `get_cookies(profile_id, domain) -> list[dict]` — reads Chromium SQLite cookie DB from volume
- `get_login_status(profile_id) -> dict[str, bool]` — checks known auth cookies per site
- `list_profiles(agent_name?) -> list[Profile]`
- `assign_agent(profile_id, agent_name) -> bool`
- `move_to_node(profile_id, node) -> bool` — stops on current node, starts on target node

### MOCK Backend

For tests and when Docker is unavailable, a `MOCK` backend that stores state in-memory and returns placeholder screenshots. Activated by environment variable or auto-detected when Docker socket is not accessible.

---

## Backend: Agent Browsers API Routes

File: `tinyagentos/routes/agent_browsers.py`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent-browsers/profiles` | List all profiles (filterable by agent_name) |
| POST | `/api/agent-browsers/profiles` | Create new profile |
| DELETE | `/api/agent-browsers/profiles/{id}` | Delete profile (stops container, keeps volume) |
| DELETE | `/api/agent-browsers/profiles/{id}/data` | Delete profile data (removes volume, with confirmation token) |
| POST | `/api/agent-browsers/profiles/{id}/start` | Start browser container |
| POST | `/api/agent-browsers/profiles/{id}/stop` | Stop browser container |
| GET | `/api/agent-browsers/profiles/{id}/screenshot` | Get CDP screenshot (PNG) |
| GET | `/api/agent-browsers/{agent}/{profile}/cookies` | Get cookies for a domain |
| GET | `/api/agent-browsers/profiles/{id}/login-status` | Get per-site login status |
| PUT | `/api/agent-browsers/profiles/{id}/assign` | Assign to agent |
| PUT | `/api/agent-browsers/profiles/{id}/move` | Move to cluster node |
| GET | `/api/agent-browsers/profiles/{id}/vnc` | WebSocket proxy for noVNC |

---

## Frontend: lib/agent-browsers.ts

Types:
```typescript
BrowserProfile { id: string; agent_name: string | null; profile_name: string; node: string; status: "stopped" | "running" | "error"; container_id: string | null; created_at: number; updated_at: number }
LoginStatus { x: boolean; github: boolean; youtube: boolean; reddit: boolean }
CookieEntry { name: string; value: string; domain: string; path: string; expires: number; httpOnly: boolean; secure: boolean }
```

Functions:
```typescript
listProfiles(agentName?: string): Promise<BrowserProfile[]>
createProfile(name: string, agentName?: string, node?: string): Promise<BrowserProfile | null>
deleteProfile(id: string): Promise<boolean>
deleteProfileData(id: string): Promise<boolean>
startBrowser(id: string): Promise<boolean>
stopBrowser(id: string): Promise<boolean>
getScreenshot(id: string): Promise<string | null>  // returns data URL
getCookies(agent: string, profile: string, domain: string): Promise<CookieEntry[]>
getLoginStatus(id: string): Promise<LoginStatus>
assignAgent(id: string, agentName: string): Promise<boolean>
moveToNode(id: string, node: string): Promise<boolean>
```

---

## Dependencies

- Docker daemon accessible from the taOS backend
- ARM64-compatible Chromium Docker image with VNC support
- noVNC client library (for the interactive connection iframe)

---

## Non-Goals

- Browser extension management (future)
- Multi-tab management (one tab per container for v1)
- Proxy/VPN configuration per browser
- Incognito/private mode (the whole point is persistent profiles)
- Automated browser tasks / Playwright integration (future — agents driving browsers)
