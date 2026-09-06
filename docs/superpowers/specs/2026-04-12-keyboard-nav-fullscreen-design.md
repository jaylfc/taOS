# Keyboard Navigation, Fullscreen Launch & Settings Redesign

## Overview

Transform taOS from a web app into a full OS experience. Users launch taOS in fullscreen with keyboard lock, navigate with standard shortcuts, and manage system settings through a macOS Ventura-style control panel. The legacy HTMX interface is removed. E2E tests validate the entire experience via Playwright.

---

## 1. Fullscreen Launch Flow

### Login Screen

The login page at `/login` serves as the entry point. After authentication, the user sees a "Launch taOS" button.

**Launch sequence:**
1. User clicks "Launch taOS"
2. `document.documentElement.requestFullscreen()` is called
3. `navigator.keyboard.lock()` is called if available
4. A warp animation plays (~600ms): the login screen scales from 1.0 to 1.15 while fading out, then the desktop fades in from opacity 0 to 1 with a scale from 0.95 to 1.0. Uses CSS `transition` on a wrapper div with `transform: scale()` and `opacity`. A subtle radial gradient overlay ("tunnel" effect) fades in and out during the transition to sell the warp feel.
5. The desktop renders

**Browser detection banners** shown on the login screen before launch:
- Chrome/Edge/Opera: "Full experience available" — green indicator
- Safari: "Install taOS as an app for the best experience" — with PWA install prompt button. Falls back to standard fullscreen if declined.
- Firefox: "For full keyboard support, use Chrome or Edge" — subtle, non-blocking info text

### Quit / Logout

A "Quit taOS" option accessible from:
- `Ctrl+Q` system shortcut (with confirmation dialog)
- Menu in the top bar
- Settings > General

**Quit sequence:**
1. Confirmation dialog: "Quit taOS? Your session will be saved."
2. `navigator.keyboard.unlock()`
3. `document.exitFullscreen()`
4. Clear session / auth tokens
5. Redirect to login screen

### Fullscreen Re-entry

If the user exits fullscreen without quitting (e.g. presses Escape on browsers without keyboard lock), a floating pill appears at the top of the screen: "Return to fullscreen" with a button. Clicking it re-enters fullscreen + re-enables keyboard lock.

### Tiered Experience

| Tier | Browsers | Fullscreen | Keyboard Lock | Fallback |
|------|----------|-----------|--------------|----------|
| 1 | Chrome, Edge, Opera | Yes | Yes — all keys captured | None needed |
| 2 | Safari | Yes (or PWA standalone) | No | Standard fullscreen, some OS combos pass through |
| 3 | Firefox | Yes | No | Standard fullscreen, banner suggesting Chrome/Edge |

---

## 2. Shortcut Registry

### Architecture

A centralized `ShortcutRegistry` implemented as a React context provider. Wraps the entire app at the root level.

**Components:**
- `ShortcutProvider` — context provider, holds the registry, attaches a single `window.addEventListener("keydown")` listener
- `useShortcut(combo, action, label, scope?)` — hook for registering a shortcut from any component. Unregisters on unmount.
- `useShortcuts()` — hook to read all registered shortcuts (for the Settings shortcut table)

### Registration

```
{
  combo: string        // e.g. "Ctrl+W", "Escape", "Ctrl+Shift+Tab"
  action: () => void
  label: string        // Human-readable, e.g. "Close window"
  scope: "system" | "app" | "overlay"
  enabled: boolean     // Can be toggled
}
```

### Priority

When a key combo is pressed, the registry checks scopes in order:
1. **overlay** — modals, search palette, launchpad, context menus. If any overlay shortcut matches, it fires and stops.
2. **app** — shortcuts registered by the focused window's app. Only fires if that window has focus.
3. **system** — always-available shortcuts (Ctrl+1-9, Ctrl+Tab, Ctrl+Q, etc.)

### Combo Normalisation

- Modifiers normalised to a canonical order: `Ctrl+Alt+Shift+Key`
- On macOS, `Meta` (Cmd) is treated as `Ctrl` for shortcut matching — taOS uses `Ctrl` universally since it owns the keyboard in fullscreen
- `event.preventDefault()` called on matched shortcuts to prevent browser/OS handling

### Keyboard Lock Integration

The registry checks for `navigator.keyboard` support on mount. If available and the document is in fullscreen, it calls `keyboard.lock()`. It exposes the lock state via context so the Settings UI can display whether full keyboard capture is active.

### Replaces

The existing `use-keyboard-shortcuts.ts` hook is removed. Its two shortcuts (`Ctrl+Space` for search, `Ctrl+L` for launchpad) are registered through the new system instead.

---

## 3. System Shortcuts

Registered at the App root via the `ShortcutProvider`.

| Shortcut | Scope | Action |
|----------|-------|--------|
| `Ctrl+Space` | system | Toggle search palette |
| `Ctrl+L` | system | Toggle launchpad |
| `Ctrl+1` through `Ctrl+9` | system | Open/focus pinned dock app by position |
| `Ctrl+Tab` | system | Cycle to next open window |
| `Ctrl+Shift+Tab` | system | Cycle to previous open window |
| `Ctrl+W` | system | Close focused window |
| `Ctrl+M` | system | Minimize focused window |
| `Ctrl+F` | system | Maximize/restore focused window |
| `Ctrl+Q` | system | Quit/logout (with confirmation) |
| `Escape` | overlay | Close topmost overlay (modal, palette, launchpad, context menu) |

### Dock App Mapping

`Ctrl+1` maps to the first pinned app in dock order, `Ctrl+2` to the second, etc. The mapping is derived from the dock's current pinned app list at the time the shortcut fires. If the app is already open, it focuses that window. If not open, it launches it.

---

## 4. Settings App Redesign

### Layout

macOS Ventura-style: left sidebar with grouped navigation, right panel with section content.

**Sidebar structure:**

```
[Search settings...]

Jay
taOS Account

── System ──────────────
  General
  Storage
  Software Update

── Display ─────────────
  Accessibility
  Desktop & Dock

── Input ───────────────
  Keyboard Shortcuts

── Security ────────────
  Privacy

── Advanced ────────────
  Backup & Restore
  Developer
```

Each sidebar item has an icon and label. Groups separated by subtle dividers with group titles. Active item highlighted.

**Right panel:** Header area with large icon + section title + description text. Below that, rows of settings — each row has an icon, label, current value or control (toggle, dropdown, input), and a chevron if it drills into a sub-page.

### Sections

**General:**
- System info table (CPU, RAM, NPU, GPU, Disk, OS) — moved from current Settings
- taOS version display
- Device name

**Storage:**
- Disk usage breakdown with progress bars (Models, Data, App Catalog, Knowledge Base, Media)
- Moved from current Settings

**Software Update:**
- Current version display
- Check for updates button
- Moved from current Settings

**Accessibility:**
- Reduce motion toggle (sets `prefers-reduced-motion` override, stored in localStorage, applied via CSS class on root)
- High contrast toggle (adds `high-contrast` class to root, adjusts `--shell-*` CSS custom properties)
- Font size scale: Small / Medium (default) / Large (adjusts root `font-size`)
- Focus indicator: "Always visible" / "Keyboard only" (default) — controls whether focus rings show on mouse click or only on Tab navigation

**Desktop & Dock:**
- Dock position: Bottom (default) / Left
- Dock icon size: Small / Medium / Large
- Wallpaper selection (if applicable)

**Keyboard Shortcuts:**
- Table of all registered shortcuts from the ShortcutRegistry: columns for Shortcut, Action, Scope
- Rebind: click a shortcut cell, press new combo, saves to localStorage
- Reset to defaults button
- Keyboard lock status indicator: "Full keyboard capture: Active/Inactive" with explanation text
- "Keyboard lock requires fullscreen mode in Chrome or Edge"

**Privacy:**
- Session management (active sessions, logout all)
- Data retention toggles (moved from current Memory settings)

**Backup & Restore:**
- Create backup button
- Restore from file upload
- Moved from current Settings

**Developer:**
- Raw YAML config editor with validate/save
- Debug mode toggle
- Moved from current Settings (Advanced)

### File Structure

The current monolithic `SettingsApp.tsx` (839 lines) is replaced:

```
desktop/src/apps/
├── SettingsApp.tsx              # Shell — sidebar + router
└── settings/
    ├── GeneralSection.tsx
    ├── StorageSection.tsx
    ├── UpdateSection.tsx
    ├── AccessibilitySection.tsx
    ├── DesktopDockSection.tsx
    ├── KeyboardShortcutsSection.tsx
    ├── PrivacySection.tsx
    ├── BackupRestoreSection.tsx
    └── DeveloperSection.tsx
```

### Scope Boundaries

Settings is for system-level configuration only. These are explicitly NOT in Settings:
- Agent management → Agents app
- Memory browsing → Memory app
- Model management → Models app
- Secrets/API keys → Secrets app
- Inference providers → Providers app
- Knowledge base → Library app
- Channels → Channels app

---

## 5. In-App Keyboard Navigation

### Shared Hooks

**`useFocusTrap(ref, active)`** — when `active` is true, Tab/Shift+Tab cycles only through focusable elements within `ref`. On mount, focuses the first interactive element. On unmount (or `active` becomes false), returns focus to the previously focused element.

Applied to: modals, dialogs, search palette, launchpad, context menus, category manager in Library app.

**`useListNav(items, onSelect, options?)`** — arrow key navigation through a list. Returns `{ selectedIndex, handlers }`. `handlers` is an `onKeyDown` to attach to the list container.

- `↑` / `↓` — move selection (wraps around)
- `Enter` / `Space` — calls `onSelect(items[selectedIndex])`
- `Home` / `End` — jump to first/last
- Type-ahead: typing a letter jumps to the first item starting with that letter

Applied to: Library item list, Store app grid, Launchpad app grid, any scrollable list of items.

### Search Palette Fix

The existing SearchPalette already has arrow key navigation but the first result is not auto-selected. Fix: when results change, set `selectedIndex` to `0` so Enter immediately launches the top result. This enables the fast flow: `Ctrl+Space` → type `se` → `Enter` → Settings opens.

### Per-App Patterns

| App | Escape | Arrow keys | Tab trapping |
|-----|--------|------------|-------------|
| Library | Detail → list, close category manager | Navigate item list | Category manager modal |
| Store | Close detail panel | Navigate app grid | Install confirmation |
| Settings | Back to section list (mobile) | Navigate section list | None (no modals) |
| Memory | Back to agent list | Navigate chunk list | None |
| Messages | Close any overlay | Navigate conversation list | None |
| Agents | Close detail panel | Navigate agent list | Config modals |

### Window-Level

- Clicking a window or using `Ctrl+1-9` sets it as the focused window
- Only the focused window's app-scope shortcuts are active
- `Escape` in the overlay scope always takes priority — closes the topmost overlay before any app handles it

---

## 6. Legacy Interface Cleanup

### What Gets Removed

**Route handlers (HTML-serving only):**
All routes in `tinyagentos/routes/` that serve Jinja2 templates. This includes `/legacy`, `/training`, `/settings`, `/config`, `/agents`, `/memory`, `/models`, `/images`, `/video`, `/store`, `/channels`, `/channel-hub`, `/chat` (HTML versions), `/cluster`, `/conversions`, `/import`, `/relationships`, `/shared-folders`, `/streaming`, `/tasks`, `/templates`, `/providers`, `/workspace`, `/setup`, `/health-check`, `/offline`, and all `/api/partials/*` routes.

**Keep:** All `/api/*` JSON endpoints — these are used by the desktop apps. The `/login` route stays (becomes the fullscreen launch page). The `/desktop` route stays.

**Templates directory:**
Delete `tinyagentos/templates/` entirely — all 44+ HTML templates and partials.

**Legacy static assets:**
Delete from `static/`: `app.css`, `manifest.json` (legacy PWA manifest), `icon-192.png`, `icon-512.png` (legacy icons).

**Keep:** `static/desktop/` (the built SPA), `static/sw.js` (service worker), `static/manifest-desktop.json`, desktop icons.

**Jinja2 dependency:**
Remove the Jinja2Templates setup from `app.py` if no remaining routes need it. Remove `jinja2` from dependencies if fully unused.

### Migration

The `/` root route already redirects to `/desktop`. After cleanup, `/` should redirect to `/login` if not authenticated, or `/desktop` if authenticated.

---

## 7. E2E Test Infrastructure

### Setup

Playwright test suite in `desktop/tests/e2e/` using system Chromium on ARM64. Config in `playwright.config.ts` pointing at the local taOS URL.

### Test Helpers

```ts
async function launchApp(page: Page, name: string): Promise<void>
  // Ctrl+Space → type name → Enter

async function focusWindow(page: Page, name: string): Promise<void>
  // Click dock button for the app

async function pressShortcut(page: Page, combo: string): Promise<void>
  // Parse combo string, send key events

async function waitForWindow(page: Page, name: string): Promise<void>
  // Wait for window title element to appear

async function getAccessibilityTree(page: Page): Promise<string>
  // page.accessibility.snapshot()
```

### Test Categories

**System shortcuts:**
- `Ctrl+1` through `Ctrl+9` opens correct pinned dock apps
- `Ctrl+Tab` cycles through open windows in order
- `Ctrl+W` closes the focused window
- `Ctrl+Space` opens search palette, `Escape` closes it
- `Ctrl+L` opens launchpad, `Escape` closes it
- `Ctrl+Q` shows quit confirmation

**Search palette:**
- Type `se` → first result is Settings → Enter opens Settings
- Type `lib` → first result is Library → Enter opens Library
- Arrow keys navigate results, Enter launches selected

**App navigation:**
- Library: click source filter → items filter → click item → detail view → Escape → back to list
- Settings: click sidebar sections, verify content changes
- Store: browse categories, click app, verify detail

**Focus management:**
- Open a modal → Tab trapped within → Escape closes → focus returns to trigger
- Open search palette → Tab stays in palette → Escape closes → focus returns
- Open two windows → click second → shortcuts go to second window

**Fullscreen flow:**
- Navigate to login → click Launch → verify fullscreen → verify keyboard lock (Chrome only) → Ctrl+Q → verify exit

### Test Execution

```bash
cd /home/jay/tinyagentos/desktop
npx playwright test tests/e2e/ --project=chromium
```

Tests are tagged `@e2e` in pytest markers (existing config supports this). They require a running taOS server.

---

## Non-Goals

- Custom key bindings beyond the shortcuts listed (future — Settings UI is ready for it but no custom binding logic in v1)
- Gamepad or touch gesture input
- Multi-monitor support
- OS-level Caps Lock remapping
- Removing JSON API endpoints (only HTML-serving routes are removed)
