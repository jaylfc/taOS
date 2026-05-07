# Desktop Shell — Web Desktop Environment

**Date:** 2026-04-09
**Status:** Draft
**Amended:** 2026-04-11 — desktop apps that query "what's available?"
(Images model dropdown, Models app download state, Agents model picker,
Activity loaded-models panel) all follow **backend-driven discovery**.
Instead of each app computing its own filesystem scan, they read from a
single live `BackendCatalog` endpoint. This also means dropdowns update
reactively when a backend loads/unloads a model, without the UI having
to refresh or the user having to hit browse. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

## Overview

A purpose-built web desktop environment that replaces the current htmx frontend with a React SPA. The browser becomes the user's primary interface — a full desktop with windows, a dock, a launchpad, and bundled OS apps. Every existing TinyAgentOS page becomes a desktop app. Streaming apps (Blender, GIMP, Code Server) run inside windows as KasmVNC iframes. Agents are first-class citizens — chat with them, open their workspace, play games against them.

The aesthetic sits between macOS and Ubuntu Budgie: subtle translucency, muted palette, macOS-style traffic light buttons, no heavy glassmorphism. Dark and light modes.

```
Browser Tab
└── Desktop Shell (React 19 + TypeScript SPA)
    ├── Top Bar — logo, global search, clock, notifications, system tray
    ├── Window Manager — float + snap zones, z-order, min/max/close
    ├── Dock — pinned apps | divider | running apps (customisable)
    ├── Launchpad — fullscreen grid, searchable, categorised
    ├── Login Screen — optional password gate
    ├── App Windows
    │   ├── Platform Apps — Messages, Agents, Store, Files, Settings, ...
    │   ├── OS Apps — Calculator, Calendar, Contacts, Browser, Media Player, ...
    │   ├── Streaming Apps — Blender, GIMP, Code Server (KasmVNC iframes)
    │   └── Games — Chess vs Agent, Wordle, Crosswords, ...
    └── System Services
        ├── VFS Client — file ops via FastAPI REST
        ├── Process Manager — open windows, app state, z-order
        ├── Notification Bus — toast stack + notification centre
        ├── Theme Engine — dark/light, accent colours, wallpaper
        └── Settings Store — dock layout, window positions, preferences
│
└── FastAPI Backend (unchanged)
    ├── REST API (/api/*)
    ├── WebSocket (/ws/*)
    └── Static file serving (SPA bundle)
```

## Visual Identity — Soft Depth

### Design Language

The visual style sits between macOS and Ubuntu Budgie. Subtle translucency without heavy blur. Muted colour palette with accent glows only on interactive elements. Premium but not showy.

### Window Chrome

- macOS-style traffic light buttons (red close, yellow minimize, green maximize/restore)
- Subtle translucent titlebar — 4-6% white overlay on dark mode, not heavy frosted glass
- Rounded corners (10px)
- Soft drop shadow on focused window, muted shadow on unfocused
- Title text centred in titlebar

### Top Bar

- Slim bar across the top of the screen, semi-transparent
- Left: TinyAgentOS logo/icon
- Centre: global search (Spotlight-style, searches apps, agents, files, messages)
- Right: clock, notification bell with badge count, system indicators (CPU, backend health)
- No heavy blur — just subtle background tint

### Dock

- Bottom-centre of screen, pill-shaped container
- Subtle translucent background with thin border
- Two zones separated by a divider: pinned apps (left) | running apps (right)
- App icons with gradient fills, soft glow on hover
- Running indicator dot below active apps
- Optional magnification on hover (user preference)
- Customisable — drag to reorder, right-click to pin/unpin

**Default pinned apps:** Messages, Agents, Files, Store, Settings

### Colour Palette

**Dark mode (default):**
- Background: deep indigo/navy gradient (#1a1b2e → #252848)
- Surface: rgba(255,255,255,0.04) to rgba(255,255,255,0.06)
- Borders: rgba(255,255,255,0.06) to rgba(255,255,255,0.08)
- Text primary: rgba(255,255,255,0.85)
- Text secondary: rgba(255,255,255,0.5)
- Accent: per-agent colours, dock icon gradients

**Light mode:**
- Background: warm off-white (#f5f5f7)
- Surface: #ffffff
- Borders: #d1d1d6
- Text primary: #1d1d1f
- Text secondary: #86868b

### Wallpaper

- Default wallpaper ships with the platform (abstract, agent-themed)
- User can change wallpaper via Settings
- Wallpaper stored server-side per user

## Window Manager

### Float + Snap Zones

Free-floating windows by default. Drag to screen edges to snap into predefined zones.

**Snap zones:**
- Drag to left/right edge → 50% width, full height
- Drag to corner → 25% (quarter)
- Double-click titlebar → maximize/restore toggle
- Drag off edge → unsnap to floating

**Snap preview:** When dragging near an edge, a translucent blue overlay shows where the window will land. Release to confirm, drag away to cancel.

### Window Lifecycle

- **Open** — click app in dock/launchpad, or keyboard shortcut
- **Minimize** — yellow traffic light or click dock icon. Window shrinks to dock with animation.
- **Maximize** — green traffic light or double-click titlebar. Window fills screen minus top bar and dock.
- **Close** — red traffic light. App component unmounts. Streaming app iframes disconnect.
- **Focus** — click window or dock icon to bring to front. Focused window gets elevated shadow.

### Z-Order

Standard desktop z-ordering. Click to focus. Top bar always on top. Dock always on top. Launchpad overlays everything when open.

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + Arrow Left` | Snap to left half |
| `Ctrl/Cmd + Arrow Right` | Snap to right half |
| `Ctrl/Cmd + Arrow Up` | Maximize |
| `Ctrl/Cmd + Arrow Down` | Restore/minimize |
| `Alt + Tab` | Window switcher overlay |
| `F11` | Fullscreen (hide top bar + dock) |
| `Ctrl/Cmd + Space` | Global search (Spotlight) |
| `Ctrl/Cmd + L` | Open Launchpad |

### Implementation

Built on **react-rnd** (the same library daedalOS uses) for drag and resize. Custom snap zone detection layer on top. Window state managed in a React context (`ProcessContext`) tracking:

```typescript
interface WindowState {
  id: string;
  appId: string;
  position: { x: number; y: number };
  size: { width: number; height: number };
  zIndex: number;
  minimized: boolean;
  maximized: boolean;
  snapped: 'left' | 'right' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | null;
  focused: boolean;
}
```

## App Registry

Every app — platform, OS, streaming, game — is registered in a central manifest that the shell uses to render dock icons, launchpad entries, and window chrome.

### App Manifest

```typescript
interface AppManifest {
  id: string;                          // unique identifier
  name: string;                        // display name
  icon: string;                        // path to SVG icon
  category: 'platform' | 'os' | 'streaming' | 'game';
  component?: string;                  // React component name (lazy loaded)
  iframeSrc?: string;                  // URL for iframe-based apps
  defaultSize: { w: number; h: number };
  minSize: { w: number; h: number };
  singleton: boolean;                  // only one instance allowed
  pinned: boolean;                     // default dock position
  launchpadOrder: number;              // position in launchpad grid
  agentInteractive?: boolean;          // can agents interact with this app
}
```

### App Types

| Type | Rendering | Examples |
|---|---|---|
| `platform` | React component (lazy loaded) | Messages, Agents, Store, Settings |
| `os` | React component wrapping vendored lib | Calculator, Calendar, Media Player |
| `streaming` | iframe pointing to KasmVNC session | Blender, GIMP, Code Server |
| `game` | React component with agent integration | Chess, Wordle, Crosswords |

### Lazy Loading

All app components are dynamically imported via `React.lazy()`. The shell bundle contains only the shell itself (top bar, dock, window manager, launchpad). App code loads on first open. This keeps initial load fast.

```typescript
const apps: Record<string, () => Promise<{ default: React.ComponentType }>> = {
  messages: () => import('./apps/Messages'),
  agents: () => import('./apps/Agents'),
  calculator: () => import('./apps/Calculator'),
  chess: () => import('./apps/Chess'),
  // ...
};
```

## Bundled Apps

### Platform Apps (existing pages → React components)

Every current TinyAgentOS htmx page becomes a React component rendered inside a desktop window. The backend API stays unchanged — only the rendering layer moves from server-side templates to client-side React.

| App | Current Page | Notes |
|---|---|---|
| Messages | `/chat` | Agent chat with channels, threads, canvas |
| Agents | `/agents` | Deploy, manage, monitor agents |
| Store | `/store` | App catalog, install frameworks/models |
| Models | `/models` | Download and manage language models |
| Files | `/files` | Workspace file browser, shared folders |
| Settings | `/settings` | System config, backup, updates, providers |
| Dashboard | `/` | KPIs, CPU/RAM, activity feed |
| Memory | `/memory` | Browse agent memories, search |
| Channels | `/channels` | Configure Telegram, Discord, etc. |
| Secrets | `/secrets` | Encrypted key storage |
| Tasks | `/tasks` | Scheduled jobs |
| Import | `/import` | Drag-and-drop file import |
| Images | `/images` | Image generation |

### OS Apps (vendored libraries + custom)

Lightweight utility apps bundled with the desktop. Each is either a vendored open-source library wrapped as a React component, or a custom-built component.

| App | Source | Library | Size |
|---|---|---|---|
| Calculator | Custom | math.js (engine) | ~170KB |
| Calendar | Vendored | tui.calendar (MIT, 12.6k stars) | ~200KB |
| Contacts | Custom | — | <10KB |
| Browser | Custom | iframe + URL bar | <5KB |
| Media Player | Vendored | Plyr (MIT, 29.7k stars) | ~30KB gz |
| Text Editor | Vendored | pell (MIT, 12k stars) | ~1.4KB gz |
| Image Viewer | Vendored | Viewer.js (MIT, 8.2k stars) | ~20KB gz |
| Terminal | Vendored | xterm.js (MIT, 20k stars) | ~200KB |

Total OS app bundle: ~630KB gzipped.

### Streaming Apps (KasmVNC iframes)

Desktop apps running in containers, streamed via KasmVNC. Each opens in a window as an iframe pointing to the KasmVNC session URL. The existing app streaming infrastructure handles container lifecycle, session management, and the agent chat sidebar.

Blender, LibreOffice, GIMP, Krita, FreeCAD, Obsidian, Excalidraw, JupyterLab, Grafana, n8n, Code Server, Terminal.

### Games (play against agents)

Games where the user can choose an agent as an opponent. The agent uses its LLM to evaluate game state and pick moves.

| Game | Library | Agent Integration |
|---|---|---|
| Chess | chess.js (BSD-2) + cm-chessboard (MIT) + js-chess-engine (MIT) | Agent receives FEN position, returns move via LLM. Can also use js-chess-engine as fallback AI. Agents can play each other in tournaments. |
| Wordle | reactle fork (MIT) | Agent guesses words. User can challenge an agent to beat their score. |
| Crosswords | crosswords-js (MIT) | Collaborative — user and agent solve together. |

More games added over time. The pattern: game renders board state, sends it to agent via the existing message/adapter system, agent returns a move.

**Agent game API:**
```json
// POST to agent adapter
{
  "type": "game_action",
  "game": "chess",
  "state": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
  "legal_moves": ["e7e5", "d7d5", "g8f6"],
  "prompt": "You are playing chess as black. The current position is shown in FEN notation. Pick your move from the legal moves list. Respond with just the move."
}
// Response
{
  "move": "e7e5",
  "commentary": "Classical response, fighting for the centre."
}
```

## Launchpad

Fullscreen overlay triggered by dock icon or `Ctrl/Cmd + L`. Blurred desktop behind.

### Layout

- Search bar at top — filters apps as you type
- Grid of app icons grouped by category (Platform, OS, Streaming, Games)
- Category headers as section dividers
- Click app to launch and dismiss launchpad
- Click outside the grid or press Escape to dismiss
- Smooth fade-in/scale animation on open/close

### Search

The search bar searches across:
- App names (fuzzy match)
- Agent names (opens agent workspace)
- Recent files (opens in appropriate app)
- Settings sections (opens Settings to that section)

This is the same search as the top bar's Spotlight — launchpad and top bar share the search service.

## Agent Integration

Agents are first-class desktop citizens, not just items in a management list.

### Agent Presence

- Each deployed agent shows an online/offline status indicator
- Agent avatars with their configured colour accent
- Running agents can have dock icons (optional, user-configurable)
- Agent activity shows in notification centre

### Agent Workspace Window

Click an agent's name anywhere (dock, Messages, Agents app) to open their workspace — a unified window with tabs for:
- Messages (DM with this agent)
- Memory (search this agent's knowledge base)
- Files (this agent's workspace files)
- Logs (live log stream)
- Tasks (this agent's scheduled jobs)
- Channels (this agent's connected channels)

This is the existing "Agent Workspace" concept, now rendered as a proper desktop window.

### Game Opponents

When opening a game, a picker lets you choose an agent as opponent. The game sends board state to the agent's adapter, the agent's LLM picks a move, and the response is rendered in the game. Agents can also play each other (spectator mode).

### Agent Notifications

Agents can push notifications to the desktop:
- Task completed
- Error / needs attention
- Message received from external channel
- Scheduled job result

Notifications appear as toast popups (bottom-right) and accumulate in the notification centre (click bell icon in top bar). Badge count on the agent's dock icon if pinned.

## System Services

### Virtual Filesystem Client

All file operations go through the FastAPI backend — no browser-local storage (IndexedDB/BrowserFS). This means files are real, server-side, and accessible to agents in their containers.

```typescript
class VFSClient {
  list(path: string): Promise<FileEntry[]>;
  read(path: string): Promise<Blob>;
  write(path: string, content: Blob): Promise<void>;
  mkdir(path: string): Promise<void>;
  delete(path: string): Promise<void>;
  move(from: string, to: string): Promise<void>;
  copy(from: string, to: string): Promise<void>;
  search(query: string, path?: string): Promise<FileEntry[]>;
}
```

Backend routes (new):
```
GET    /api/vfs/list?path=...         — list directory
GET    /api/vfs/read?path=...         — read file
POST   /api/vfs/write                 — write file (multipart)
POST   /api/vfs/mkdir                 — create directory
DELETE /api/vfs/delete?path=...       — delete file/directory
POST   /api/vfs/move                  — move/rename
POST   /api/vfs/copy                  — copy
GET    /api/vfs/search?q=...&path=... — search files
```

### Process Manager

Tracks all open windows and their state. Persists dock layout and window positions to the backend so they restore on page reload.

```typescript
interface ProcessManager {
  open(appId: string, props?: Record<string, any>): string;  // returns windowId
  close(windowId: string): void;
  focus(windowId: string): void;
  minimize(windowId: string): void;
  maximize(windowId: string): void;
  getWindows(): WindowState[];
  getRunningApps(): string[];  // app IDs with open windows
}
```

### Notification Bus

Central notification system. Sources: agents, system events, app events. Sinks: toast popups, notification centre, dock badges.

```typescript
interface Notification {
  id: string;
  source: string;       // agent ID, 'system', or app ID
  title: string;
  body: string;
  icon?: string;
  action?: string;      // URL or app to open on click
  read: boolean;
  timestamp: number;
}
```

### Theme Engine

Manages dark/light mode, accent colours, and wallpaper.

```typescript
interface ThemeSettings {
  mode: 'dark' | 'light' | 'system';
  accentColor: string;
  wallpaper: string;          // URL to wallpaper image
  dockMagnification: boolean;
  dockPosition: 'bottom';    // future: 'left', 'right'
  topBarOpacity: number;      // 0.0 - 1.0
}
```

### Settings Store

Persists all user preferences server-side via:
```
GET  /api/desktop/settings           — get all settings
PUT  /api/desktop/settings           — update settings
GET  /api/desktop/dock               — get dock layout
PUT  /api/desktop/dock               — update dock layout
GET  /api/desktop/windows            — get saved window positions
PUT  /api/desktop/windows            — save window positions
```

## Login Screen

Optional — configured during first-time setup wizard. When enabled:

- Full-screen login view with wallpaper background
- Username (or just password for single-user mode)
- Session persisted via cookie (same auth system as current TinyAgentOS)
- Architecture is multi-user ready — user context flows through all API calls
- Full multi-user system (accounts, roles, managed accounts, per-user API keys) is a separate spec

## Project Templates (Future)

Export a project setup as a shareable template:
- Agent configuration (framework, model, system prompt, skills)
- Memory seed (initial documents / knowledge base)
- Channel configuration
- File structure
- Skill assignments

Import templates from a community marketplace or shared repository. This extends the existing Projects feature with portability.

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| UI framework | React 19 | Lazy-loaded app components |
| Language | TypeScript | Strict mode |
| Build | Vite | Fast dev server, optimised production build |
| Window management | react-rnd | Drag + resize, used by daedalOS |
| Styling | Tailwind CSS 4 | Utility-first, good for theming |
| State | React Context + zustand | Shell state (windows, dock, theme) |
| API client | fetch / SWR | REST + WebSocket to FastAPI |
| Icons | Lucide React | Clean, consistent icon set |
| PWA | Service worker | Offline shell, installable |

The service worker precaches the shell HTML (`/desktop`, `/desktop/index.html`, `/chat-pwa`) and all hashed JS/CSS assets on install. Caches are namespaced with `__TAOS_VERSION__` so the activate step evicts any stale cache from a previous build. When a new build is deployed the SW detects the version mismatch on next load and shows a toast prompting the user to reload — they never silently get stale assets. The shell HTML is intentionally pre-login accessible (no auth gate) so the SW can cache it on first visit; the SPA enforces auth client-side via `/auth/status` and redirects to `/auth/login` when there is no valid session.
| Backend | FastAPI (unchanged) | Serves SPA bundle as static files |

## Migration Path

The htmx → React migration is the largest piece of work. Approach:

1. **Shell first** — build the desktop shell (top bar, dock, window manager, launchpad) as a standalone SPA. Mount it at a new route (`/desktop`) alongside the existing htmx UI.
2. **Platform apps one by one** — migrate each page (starting with Messages, Agents, Store) from htmx template to React component. Each migrated page opens as a desktop window.
3. **OS apps** — add calculator, calendar, media player, etc. as new React components.
4. **Games** — add chess, wordle, etc. with agent integration.
5. **Cut over** — once all pages are migrated, make the desktop the default route (`/`). Remove htmx templates.
6. **Streaming apps** — already iframe-based, just need window wrappers.

During migration, both UIs coexist. The API layer doesn't change at any point.

## New Backend Routes

```
# Desktop state
GET  /api/desktop/settings              — user desktop preferences
PUT  /api/desktop/settings              — update preferences
GET  /api/desktop/dock                  — dock layout
PUT  /api/desktop/dock                  — update dock layout
GET  /api/desktop/windows               — saved window positions
PUT  /api/desktop/windows               — save window positions

# Virtual filesystem
GET    /api/vfs/list?path=...           — list directory
GET    /api/vfs/read?path=...           — read file
POST   /api/vfs/write                   — write file
POST   /api/vfs/mkdir                   — create directory
DELETE /api/vfs/delete?path=...         — delete
POST   /api/vfs/move                    — move/rename
POST   /api/vfs/copy                    — copy
GET    /api/vfs/search?q=...            — search files

# Games
POST /api/games/{game}/move             — send game state to agent, get move back
GET  /api/games/{game}/history          — past games and results
```

## Responsive Modes — Mobile & Tablet

The shell adapts to the device. Like KDE's desktop vs tablet mode, TinyAgentOS detects screen size and input method and switches layout accordingly.

### Three Modes

| Mode | Trigger | Layout |
|---|---|---|
| **Desktop** | Screen width >= 1024px, no primary touch | Full windowed desktop — top bar, dock, floating/snapping windows |
| **Tablet** | Screen width >= 768px, primary touch input | Fullscreen apps with gesture navigation, dock as app switcher |
| **Mobile** | Screen width < 768px | Single app at a time, bottom tab bar, swipe between apps |

Mode is auto-detected but user can override in Settings ("Force desktop mode on tablet").

### Tablet Mode

- **No floating windows** — each app runs fullscreen or split-view (two apps side-by-side)
- **Dock becomes app switcher** — swipe up from bottom edge to reveal dock. Tap to switch. Long-press for app options.
- **Split view** — drag an app from the dock onto the left or right edge to enter 50/50 split. Like iPadOS Split View.
- **Gesture navigation:**
  - Swipe up from bottom → app switcher (dock)
  - Swipe up and hold → launchpad
  - Swipe from left edge → back (within app)
  - Pinch to zoom in supported apps (image viewer, browser, canvas)
- **Top bar** — slimmer, no search bar (search via launchpad). Clock and notifications remain.
- **Multitouch** — pinch-to-zoom, two-finger scroll, rotation gestures in supported apps (image viewer, canvas, maps)

### Mobile Mode

- **Single app fullscreen** — no windows, no split view
- **Bottom tab bar** — replaces dock. Shows 4-5 pinned apps + "more" button for launchpad
- **Swipe navigation** — swipe right to go back, swipe up for app switcher
- **Top bar** — minimal: app name + back button + notifications bell
- **Pull down** — notification centre

### Touch Support (All Modes)

- **Touch targets** — all interactive elements minimum 44x44px on touch devices
- **Long press** — context menus (right-click equivalent)
- **Drag and drop** — touch-hold then drag for file moves, dock reordering
- **Scroll momentum** — native-feeling inertial scroll in all scrollable areas
- **No hover states on touch** — hover effects only on pointer devices (via `@media (hover: hover)`)

### Implementation Approach

The shell uses a `useDeviceMode()` hook that returns `"desktop" | "tablet" | "mobile"` based on:
1. `window.innerWidth` breakpoints
2. `navigator.maxTouchPoints > 0` for touch detection
3. `matchMedia("(pointer: coarse)")` for primary input type
4. User override from settings

Each shell component (TopBar, Dock, Desktop, Window) renders differently per mode. This is NOT a separate app — it's the same React SPA with responsive variants. The app registry stays the same; only the shell chrome adapts.

```typescript
function useDeviceMode(): "desktop" | "tablet" | "mobile" {
  // Check user override first
  // Then: width >= 1024 && !coarsePointer → desktop
  //       width >= 768 → tablet
  //       else → mobile
}
```

The desktop mode is built first (this plan). Tablet and mobile modes are a follow-up plan that adds responsive variants to each component.

## Non-Goals (This Spec)

- Full multi-user/tenant system (accounts, roles, family management) — separate spec
- Voice/video calls between windows
- Custom app development SDK for third parties
- Offline-first with local caching (PWA provides basic offline shell, but apps need the server)
- Project templates marketplace implementation — noted as future direction
- Electron/Tauri wrapper — browser-only for now
