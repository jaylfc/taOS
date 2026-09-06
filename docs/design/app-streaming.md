# Containerised App Streaming with Agent Integration

**Status:** In progress — tracked by #22. The session / workspace / agent-bridge / orchestration layers are shipped; containerised runtime is the remaining piece.

## Overview

Wrap desktop applications in Docker containers with pre-wired MCP servers, stream them to any device via the browser, and let agents interact with them through structured tools, batch commands, keyboard injection, and optional computer-use vision.

**Goal:** Install "Blender" from the TinyAgentOS store and get Blender + Blender MCP server + agent integration in one click. Stream it to any device. Collaborate with an agent on files. Swap agents mid-session without restarting the app.

## Architecture

```
User's Browser
├── App Stream (KasmVNC canvas, left panel)
├── Agent Chat (sidebar, right panel)
└── Agent selector dropdown (Expert / User agents)
        │
        ▼
TinyAgentOS Controller (Orange Pi / server)
├── Session Manager (SQLite: session_id, app, agent, worker, status)
├── WebSocket Proxy (/ws/app/{session_id} → worker:6901)
├── OCI Registry (port 5000, serves app images to workers)
├── File Sync (rsync/HTTP, controller = source of truth)
├── User Workspace (/data/workspace/ — NAS-like file storage)
└── App Expert Agent Manager (hidden agents, per-app)
        │
        ▼
Worker Node (GPU PC, Mac, SBC, any device)
├── Container Runtime (Podman on Linux, Docker on Windows/Mac)
│   └── App Container
│       ├── KasmVNC server (port 6901)
│       ├── The app (Blender, GIMP, etc.)
│       ├── MCP server (pre-configured for this app)
│       ├── taos-agent-bridge (port 9100)
│       ├── xdotool + scrot + xclip
│       ├── Openbox window manager
│       └── PulseAudio
├── GPU passthrough (NVIDIA/AMD/NPU)
├── Cached app images (from controller's OCI registry)
└── Temporary file working directory (synced to/from controller)
```

## 1. Container Format

### Base Image (`taos-app-base`)

Multi-arch (ARM64 + AMD64) base image providing the streaming and agent infrastructure:

- Ubuntu 22.04 Jammy
- KasmVNC server (WebSocket on port 6901, WebP encoding, audio via PulseAudio)
- Openbox window manager (minimal, no desktop environment)
- xdotool, xclip, scrot, imagemagick (agent system tools)
- Python 3.11 + Node 20 (for MCP servers)
- taos-agent-bridge daemon (see Section 3)
- Mount points: `/workspace/`, `/agent-files/`, `/shared/`
- Estimated compressed size: ~800MB-1.1GB

### App Image (e.g., `taos-app-blender`)

```dockerfile
FROM taos-app-base:latest
RUN apt-get update && apt-get install -y blender
RUN pip install blender-mcp
COPY mcp-config.json /etc/taos/mcp.json
COPY startup.sh /etc/taos/startup.sh
LABEL taos.app.id="blender"
LABEL taos.app.version="4.3.0"
LABEL taos.mcp.server="blender-mcp"
LABEL taos.computer_use="optional"
```

### App Manifest Extension

The existing YAML app manifest format gains a `streaming` section:

```yaml
id: blender
name: Blender
type: streaming-app
version: 4.3.0
description: "3D modelling, animation, and rendering with AI agent assistance"
homepage: https://www.blender.org
license: GPL-2.0

streaming:
  backend: kasmvnc
  port: 6901
  resolution: 1920x1080
  audio: true

mcp:
  server: blender-mcp
  install: "pip install blender-mcp"
  capabilities:
    - create_object
    - set_material
    - manipulate_scene
    - execute_python
    - export_file

agent_bridge:
  port: 9100
  computer_use: optional    # disabled | optional | required
  exec_enabled: true
  keyboard_enabled: true

expert_agent:
  name: "Blender Expert"
  system_prompt: "You are a Blender expert assistant. Help users with 3D modelling, materials, lighting, rendering, and animation. Use MCP tools when available. Suggest keyboard shortcuts for common operations. When stuck, offer to use computer use mode."
  model: qwen3-4b
  color: "#E87D0D"

requires:
  ram_mb: 2048
  gpu_recommended: true

install:
  method: docker
  image: taos-app-blender

hardware_tiers:
  x86-cuda-8gb: full
  x86-cuda-12gb: full
  x86-vulkan-8gb: full
  arm-npu-16gb: full
  apple-silicon: full
  cpu-only: limited
```

## 2. Streaming Infrastructure

### Launch Flow

1. User clicks "Launch Blender" (dashboard, companion app, or workspace file "Open with...")
2. TinyAgentOS picks the best worker: GPU app goes to GPU worker, CPU-only app runs locally
3. If the image isn't cached on the worker, pull from controller's OCI registry over LAN
4. Sync workspace files and agent files to the worker via rsync/HTTP
5. Start container with: GPU passthrough, workspace bind mount, agent files mount, network config
6. Container boots: KasmVNC + Openbox + PulseAudio + MCP server + agent-bridge + the app
7. Controller registers the session (SQLite) and returns a session URL
8. Browser opens `/app/{session_id}`: stream on left, chat panel on right

### Streaming Page Layout

```
+----------------------------------------------------------+
| TinyAgentOS  [Blender v4.3]  [Blender Expert ▼] [x Close]|
+----------------------------------+-----------------------+
|                                  | Blender Expert        |
|   KasmVNC Canvas                 | ─────────────────     |
|   (WebSocket: /ws/app/{id})      | "How can I help with  |
|                                  |  your 3D project?"    |
|   - Full app streaming           |                       |
|   - Keyboard/mouse input         | [Screenshot] [Undo]   |
|   - Audio                        | [Computer Use: Off]   |
|                                  |                       |
|                                  | > Type message...     |
+----------------------------------+-----------------------+
```

The agent selector dropdown shows:
- App expert agent (default, always available)
- User's deployed agents (fetched from `/api/agents`)
- "Detach agent" option (use app without AI assistance)

### WebSocket Proxy

TinyAgentOS proxies the KasmVNC WebSocket through the main port:
- `/ws/app/{session_id}` on port 6969 proxied to `worker_ip:6901`
- Auth handled at TinyAgentOS level (session cookie), not per-container
- Single port exposure, no firewall changes needed

### Session Management

Stored in SQLite (`streaming_sessions` table):
- session_id, app_id, agent_name, agent_type (expert/user), worker_name, container_id
- status (starting/running/paused/stopped), started_at, last_activity
- Idle timeout: container paused after 30min, resumed on reconnect
- Active sessions visible on dashboard and companion app

## 3. Agent Interaction Layer

### taos-agent-bridge

A small Python daemon (~150 lines) running inside each app container on port 9100. Single point of contact between TinyAgentOS and the app.

**MCP tools (structured, cheapest):**
```
POST /mcp/tool           → call an MCP tool by name with args
GET  /mcp/capabilities   → list available tools for this app
```

**System tools (batch operations, efficient):**
```
POST /exec               → run shell command (e.g., "find /workspace -name '*.blend'")
POST /files/read         → read file contents
POST /files/write        → write file
POST /files/list         → list directory
POST /files/batch        → batch command (mv, cp, rename, tar, etc.)
```

**Visual tools (for confirmation and computer-use):**
```
GET  /screenshot         → capture current screen (PNG)
POST /keyboard           → inject shortcut ({"keys": "ctrl+s"})
POST /mouse              → click at x,y coordinates
POST /type               → type text string
```

**Session tools:**
```
GET  /health             → bridge + app + MCP server status
POST /agent/swap         → prepare for agent hot-swap (flush state, swap symlink)
GET  /agent/current      → current attached agent info
POST /computer-use       → toggle computer-use mode
GET  /computer-use       → current toggle state
```

### Agent Tool Priority

The agent uses tools in this order, preferring the cheapest/most reliable:

1. **MCP tool** — structured, typed, reliable. Used when a tool exists for the action.
2. **Batch exec** — for bulk file ops, installs, system commands. `POST /exec` with shell command. Avoids burning tokens on individual file moves.
3. **Keyboard shortcut** — `POST /keyboard` with xdotool. Fast for save, undo, export, zoom, navigation.
4. **Screenshot on request** — `GET /screenshot` when agent needs visual confirmation. Uses vision model. Expensive but sometimes necessary.
5. **Full computer-use** — screenshot + mouse + keyboard loop. Only when computer_use toggle is ON. Agent can suggest enabling it after 3 failed MCP attempts.

### Agent Escalation

If the agent fails an action 3 times via MCP:
- Chat message: "I'm having trouble getting [action] to work through the API. Would you like me to try computer use to see what's happening on screen? I can look at the app directly and try to figure out what's going on."
- User approves or declines in the chat
- If approved, computer-use toggle flips on for this session
- Agent takes a screenshot, analyses with vision, attempts the action via mouse/keyboard
- Reports result in chat with the screenshot

## 4. User Workspace

### File Browser (`/workspace`)

A NAS-like file management page where users upload, organise, and manage their files:

- Drag-and-drop upload
- Directory tree browser
- Preview for images, text, PDFs
- "Open with..." context menu (lists installed streaming apps)
- Per-file sharing (share with agents, share with other users)
- Storage quota display

### Mount Structure Inside Containers

```
/workspace/              → user's full workspace (read/write)
/workspace/blender/      → app-specific default save location
/agent-files/            → attached agent's file storage (hot-swappable)
/shared/                 → shared folders the user/agent both access
```

### File Sync (Remote Workers)

Controller is the source of truth for all files. When a session starts on a remote worker:

1. Essential files synced to worker before container starts (rsync over SSH or HTTP)
2. During session, file writes go to a local working directory on the worker
3. Periodic sync (every 60s) pushes changes back to controller
4. On session end, final sync back to controller
5. If worker crashes, controller has the last synced state

## 5. Agent Types

### App Expert Agents

- Created automatically when a streaming app is first launched
- Hidden from `/agents` list and agent APIs (filtered by `type: "app-expert"`)
- Managed via the app's settings panel in the streaming page
- Each has its own QMD memory, file storage, message history
- System prompt tailored to the app (Blender Expert knows Blender, GIMP Expert knows GIMP)
- Can be customised: edit system prompt, view/reset memory, clear workspace

**App settings panel:**
```
Expert Agent: Blender Expert
├── View Memory          (memory browser filtered to this expert)
├── View Workspace       (file browser for expert's storage)
├── Edit System Prompt   (customise personality/knowledge)
├── Reset Memory         (clear learned context, fresh start)
├── Computer Use: [Off]  (toggle for this app)
└── Advanced: exec/keyboard toggles
```

### User Agent Attachment

Users can attach any of their deployed agents to an app session:
- Dropdown in the streaming page header
- Hot-swappable without restarting the app or container

**Hot-swap mechanism (host-side symlink):**
1. A host directory `/data/app-sessions/{session_id}/agent-files/` is bind-mounted into the container at `/agent-files/`
2. This host directory is a symlink pointing to the current agent's file storage
3. On swap: agent-bridge flushes MCP state, TinyAgentOS swaps the symlink target, agent-bridge reloads
4. Chat panel loads the new agent's conversation history
5. The container never restarts

Multi-host upgrade path: use NFS/SSHFS mounts instead of symlinks when agents are on different workers.

## 6. Worker Infrastructure

### Container Runtime

Workers need a container runtime to run app containers. The worker app handles this:

- **Linux:** Podman (rootless, daemonless, ships as single binary). Auto-installed by worker setup script.
- **Windows:** Docker Desktop or Podman Machine. Bundled in the worker installer.
- **macOS:** Podman Machine. Bundled in the worker installer.

### GPU Passthrough

Worker app detects GPUs and configures container flags:
- **NVIDIA:** `--gpus all` (nvidia-container-toolkit auto-installed by worker setup)
- **AMD:** `--device /dev/kfd --device /dev/dri`
- **Apple Silicon:** GPU access via Podman Machine's Virtualization.framework

### Image Distribution

TinyAgentOS controller runs a lightweight OCI registry (port 5000):
- When an app is installed from the store, the image is built/pulled on the controller
- Workers pull from this LAN registry, not the internet
- Images cached on workers after first pull
- Controller can push updated images to workers

### Worker Install Experience

```bash
# Linux (one command)
curl -sL https://tinyagentos.com/worker | bash
# Installs: worker app + podman + nvidia-container-toolkit (if NVIDIA detected)

# Windows (installer)
tinyagentos-worker-setup.exe
# Installs: worker app + container runtime + GPU drivers if needed

# macOS (installer)
brew install tinyagentos-worker
# Or: download .dmg from tinyagentos.com
```

User runs installer, enters server URL, done. First app launch pulls the image (1-2 min on LAN), subsequent launches are instant.

## 7. Priority App List

### Phase 1 (Proof of Concept — 4 apps)

| App | Category | MCP Server | Why First |
|-----|----------|-----------|-----------|
| Blender | Creative/3D | blender-mcp (official, 17k+ stars) | Best MCP coverage, impressive demos |
| LibreOffice | Productivity | libreoffice-containerized-mcp-server | Daily-use, documents, spreadsheets |
| Code Server | Development | VS Code native MCP | Already in catalog, agent-natural |
| GIMP | Creative | gimp-mcp (GObject bindings) | Image editing, pairs with image gen |

### Phase 2 (Core Suite — 8 apps)

| App | Category | MCP Server |
|-----|----------|-----------|
| Krita | Creative | krita-mcp |
| FreeCAD | Creative/3D | freecad-mcp (617 stars) |
| Obsidian | Productivity | obsidian-mcp (10+ servers) |
| Excalidraw | Productivity | mcp_excalidraw |
| Jupyter | Data Science | jupyter-mcp-server |
| Grafana | Data/Monitoring | grafana-mcp (official) |
| n8n | Automation | API-based |
| Terminal | DevOps | Desktop Commander MCP |

### Phase 3 (Extended — 10+ apps)

| App | Category | MCP Server |
|-----|----------|-----------|
| Inkscape | Creative | inkscape-mcps |
| OpenSCAD | Creative/3D | openscad-mcp |
| OBS Studio | Media | obs-mcp (WebSocket API) |
| VLC | Media | vlc-mcp-server |
| FFmpeg (headless) | Media | video-audio-mcp |
| Audacity | Media | None (computer-use required) |
| Penpot | Design | penpot-mcp (official) |
| Home Assistant | IoT | hass-mcp |
| Godot | Game Dev | Community MCP |
| Kdenlive | Video Edit | None (computer-use required) |

### Future Consideration

| App | Notes |
|-----|-------|
| Figma | Official MCP, but SaaS — stream via browser MCP rather than container |
| Canva | Official MCP, same as Figma |
| DaVinci Resolve | No MCP, heavy GPU, computer-use only |

## 8. Future: Xpra Integration

KasmVNC streams the entire desktop as a single canvas. Xpra streams individual windows as separate DOM elements. This opens up:

- Each app window becomes a targetable DOM node (agent can reference UI elements by DOM, not pixel coords)
- Natural overlay/sidebar without canvas z-index tricks
- Multiple app windows can be independently positioned in the browser
- Potential for the agent to "read" the DOM representation of the app window

Planned as an alternative streaming backend. The container format and agent-bridge API remain the same. Could contribute upstream to Xpra for agent-specific features.

## Non-Goals (for now)

- Full desktop environment (we stream individual apps, not a desktop)
- Multi-user concurrent sessions on the same app instance
- App-to-app communication (agents bridge between apps, not direct IPC)
- Mobile-native streaming client (browser-based is sufficient for v1)
- Building MCP servers for apps that don't have one (use computer-use instead)
