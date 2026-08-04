# Desktop Shell Core — Implementation Plan

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the desktop shell foundation — React SPA with top bar, dock, window manager (float + snap), launchpad, and process manager — that can host app windows. This plan does NOT migrate existing pages to React. It delivers a working desktop shell with a few placeholder apps so the window system can be tested end-to-end.

**Architecture:** React 19 + TypeScript SPA built with Vite, served as static files by FastAPI. The shell runs in the browser and communicates with the existing FastAPI backend via REST/WebSocket. Window management uses react-rnd for drag/resize with a custom snap zone layer. State managed via zustand. The SPA mounts at `/desktop` during development (coexists with htmx UI), becomes `/` after full migration.

**Tech Stack:** React 19, TypeScript, Vite 6, react-rnd, zustand, Tailwind CSS 4, Lucide React icons

---

## File Structure

```
desktop/                              # New directory at project root
├── package.json                      # Node dependencies
├── tsconfig.json                     # TypeScript config
├── vite.config.ts                    # Vite build config — outputs to ../static/desktop/
├── tailwind.config.ts                # Tailwind config with custom theme
├── index.html                        # SPA entry point
├── src/
│   ├── main.tsx                      # React root mount
│   ├── App.tsx                       # Desktop shell root — top bar + desktop + dock
│   ├── theme/
│   │   ├── tokens.css                # CSS custom properties — colours, spacing, radii
│   │   └── tailwind-preset.ts        # Tailwind preset with Soft Depth theme
│   ├── stores/
│   │   ├── process-store.ts          # zustand — open windows, z-order, focus
│   │   ├── dock-store.ts             # zustand — pinned apps, running apps
│   │   ├── theme-store.ts            # zustand — dark/light, wallpaper, accent
│   │   └── notification-store.ts     # zustand — toast stack, notification list
│   ├── registry/
│   │   └── app-registry.ts           # App manifest definitions + lazy imports
│   ├── components/
│   │   ├── TopBar.tsx                # Top bar — logo, search, clock, notifications
│   │   ├── Dock.tsx                  # Bottom dock — pinned + running apps
│   │   ├── DockIcon.tsx              # Single dock icon with tooltip + indicator
│   │   ├── Desktop.tsx               # Desktop surface — wallpaper + windows
│   │   ├── Window.tsx                # Window chrome — titlebar, traffic lights, content
│   │   ├── WindowContent.tsx         # Renders app component or iframe inside window
│   │   ├── SnapOverlay.tsx           # Snap zone preview overlay during drag
│   │   ├── Launchpad.tsx             # Fullscreen app grid overlay
│   │   ├── LaunchpadIcon.tsx         # Single launchpad app icon
│   │   ├── SearchPalette.tsx         # Spotlight-style search (Ctrl+Space)
│   │   ├── NotificationToast.tsx     # Toast popup (bottom-right)
│   │   └── NotificationCentre.tsx    # Dropdown notification list
│   ├── hooks/
│   │   ├── use-snap-zones.ts         # Snap zone detection during drag
│   │   ├── use-clock.ts              # Live clock for top bar
│   │   └── use-keyboard-shortcuts.ts # Global keyboard shortcut handler
│   ├── apps/
│   │   ├── PlaceholderApp.tsx        # Generic placeholder for unmigrated apps
│   │   └── IframeApp.tsx             # Generic iframe wrapper for streaming apps
│   └── lib/
│       └── api.ts                    # Fetch wrapper for FastAPI backend
├── tests/
│   ├── process-store.test.ts         # Unit tests for process/window state
│   ├── dock-store.test.ts            # Unit tests for dock state
│   ├── snap-zones.test.ts            # Unit tests for snap zone logic
│   └── app-registry.test.ts          # Unit tests for app registry
```

**Backend additions (minimal):**
```
tinyagentos/
├── desktop_settings.py               # DesktopSettingsStore — dock layout, preferences
├── routes/
│   └── desktop.py                    # /api/desktop/* routes + SPA catch-all
```

---

## Task 1: Scaffold Vite + React + TypeScript Project

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/tsconfig.json`
- Create: `desktop/vite.config.ts`
- Create: `desktop/index.html`
- Create: `desktop/src/main.tsx`
- Create: `desktop/src/App.tsx`
- Create: `desktop/.gitignore`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "tinyagentos-desktop",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-rnd": "^10.4.0",
    "zustand": "^5.0.0",
    "lucide-react": "^0.500.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.5.0",
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/vite": "^4.0.0"
  }
}
```

- [ ] **Step 2: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "paths": {
      "@/*": ["./src/*"]
    },
    "baseUrl": "."
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Create vite.config.ts**

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "../static/desktop",
    emptyDirBeforeWrite: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:6969",
      "/ws": { target: "ws://localhost:6969", ws: true },
    },
  },
});
```

- [ ] **Step 4: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>TinyAgentOS</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create src/main.tsx**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./theme/tokens.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Create src/App.tsx (minimal shell)**

```tsx
export function App() {
  return (
    <div className="h-screen w-screen bg-shell-bg text-shell-text overflow-hidden">
      <div className="text-center pt-20 text-white/50">
        TinyAgentOS Desktop Shell
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Create desktop/.gitignore**

```
node_modules/
dist/
```

- [ ] **Step 8: Install dependencies and verify dev server starts**

Run:
```bash
cd desktop && npm install && npm run dev -- --port 5173 &
sleep 3 && curl -s http://localhost:5173 | head -5
kill %1
```
Expected: HTML response containing `<div id="root">`.

- [ ] **Step 9: Commit**

```bash
git add desktop/
git commit -m "scaffold desktop shell: Vite + React 19 + TypeScript + Tailwind"
```

---

## Task 2: Theme Tokens + Tailwind Preset

**Files:**
- Create: `desktop/src/theme/tokens.css`
- Create: `desktop/src/theme/tailwind-preset.ts`

- [ ] **Step 1: Create tokens.css**

```css
@import "tailwindcss";

@theme {
  /* Background layers */
  --color-shell-bg: #1a1b2e;
  --color-shell-bg-deep: #151625;
  --color-shell-surface: rgba(255, 255, 255, 0.04);
  --color-shell-surface-hover: rgba(255, 255, 255, 0.06);
  --color-shell-surface-active: rgba(255, 255, 255, 0.08);
  --color-shell-border: rgba(255, 255, 255, 0.06);
  --color-shell-border-strong: rgba(255, 255, 255, 0.1);

  /* Text */
  --color-shell-text: rgba(255, 255, 255, 0.85);
  --color-shell-text-secondary: rgba(255, 255, 255, 0.5);
  --color-shell-text-tertiary: rgba(255, 255, 255, 0.3);

  /* Traffic lights */
  --color-traffic-close: #ff5f57;
  --color-traffic-minimize: #febc2e;
  --color-traffic-maximize: #28c840;

  /* Accent */
  --color-accent: #667eea;
  --color-accent-glow: rgba(102, 126, 234, 0.3);

  /* Dock */
  --color-dock-bg: rgba(255, 255, 255, 0.06);
  --color-dock-border: rgba(255, 255, 255, 0.06);

  /* Snap zones */
  --color-snap-preview: rgba(102, 126, 234, 0.15);
  --color-snap-border: rgba(102, 126, 234, 0.4);

  /* Spacing */
  --spacing-topbar-h: 32px;
  --spacing-dock-h: 64px;
  --spacing-dock-padding: 8px;
  --spacing-window-radius: 10px;
  --spacing-dock-radius: 16px;

  /* Shadows */
  --shadow-window: 0 8px 32px rgba(0, 0, 0, 0.4);
  --shadow-window-unfocused: 0 4px 16px rgba(0, 0, 0, 0.2);
  --shadow-dock: 0 4px 24px rgba(0, 0, 0, 0.3);
}
```

- [ ] **Step 2: Verify Tailwind picks up custom tokens**

Run:
```bash
cd desktop && echo '<div class="bg-shell-bg text-shell-text">test</div>' > /tmp/tw-test.html && npx tailwindcss --content /tmp/tw-test.html --output /tmp/tw-out.css 2>&1 | head -5
```
Expected: No errors. CSS output contains the custom colour values.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/theme/
git commit -m "add Soft Depth theme tokens for desktop shell"
```

---

## Task 3: Process Store (Window State Management)

**Files:**
- Create: `desktop/src/stores/process-store.ts`
- Create: `desktop/tests/process-store.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// desktop/tests/process-store.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { useProcessStore } from "../src/stores/process-store";

beforeEach(() => {
  useProcessStore.setState({ windows: [], nextZIndex: 1 });
});

describe("process store", () => {
  it("opens a window and assigns z-index", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    const { windows } = useProcessStore.getState();
    expect(windows).toHaveLength(1);
    expect(windows[0].appId).toBe("messages");
    expect(windows[0].zIndex).toBe(1);
    expect(windows[0].size).toEqual({ w: 900, h: 600 });
    expect(id).toBe(windows[0].id);
  });

  it("closes a window by id", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().closeWindow(id);
    expect(useProcessStore.getState().windows).toHaveLength(0);
  });

  it("focuses a window — moves to top z-index", () => {
    const { openWindow } = useProcessStore.getState();
    const id1 = openWindow("messages", { w: 900, h: 600 });
    const id2 = openWindow("agents", { w: 800, h: 500 });
    useProcessStore.getState().focusWindow(id1);
    const { windows } = useProcessStore.getState();
    const w1 = windows.find((w) => w.id === id1)!;
    const w2 = windows.find((w) => w.id === id2)!;
    expect(w1.zIndex).toBeGreaterThan(w2.zIndex);
    expect(w1.focused).toBe(true);
    expect(w2.focused).toBe(false);
  });

  it("minimizes and restores a window", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().minimizeWindow(id);
    expect(useProcessStore.getState().windows[0].minimized).toBe(true);
    useProcessStore.getState().restoreWindow(id);
    expect(useProcessStore.getState().windows[0].minimized).toBe(false);
  });

  it("maximizes and restores a window", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().maximizeWindow(id);
    expect(useProcessStore.getState().windows[0].maximized).toBe(true);
    useProcessStore.getState().maximizeWindow(id); // toggle
    expect(useProcessStore.getState().windows[0].maximized).toBe(false);
  });

  it("enforces singleton — does not open duplicate", () => {
    const { openWindow } = useProcessStore.getState();
    openWindow("messages", { w: 900, h: 600 });
    const id2 = openWindow("messages", { w: 900, h: 600 });
    const { windows } = useProcessStore.getState();
    expect(windows).toHaveLength(1);
    // Should have focused the existing one instead
    expect(windows[0].focused).toBe(true);
    expect(id2).toBe(windows[0].id);
  });

  it("updates window position", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().updatePosition(id, 100, 200);
    const w = useProcessStore.getState().windows[0];
    expect(w.position).toEqual({ x: 100, y: 200 });
  });

  it("updates window size", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().updateSize(id, 1000, 700);
    const w = useProcessStore.getState().windows[0];
    expect(w.size).toEqual({ w: 1000, h: 700 });
  });

  it("sets snap state", () => {
    const { openWindow } = useProcessStore.getState();
    const id = openWindow("messages", { w: 900, h: 600 });
    useProcessStore.getState().snapWindow(id, "left");
    expect(useProcessStore.getState().windows[0].snapped).toBe("left");
    useProcessStore.getState().snapWindow(id, null);
    expect(useProcessStore.getState().windows[0].snapped).toBeNull();
  });

  it("returns running app IDs", () => {
    const { openWindow } = useProcessStore.getState();
    openWindow("messages", { w: 900, h: 600 });
    openWindow("agents", { w: 800, h: 500 });
    expect(useProcessStore.getState().runningAppIds()).toEqual([
      "messages",
      "agents",
    ]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npx vitest run tests/process-store.test.ts 2>&1 | tail -5`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement process store**

```typescript
// desktop/src/stores/process-store.ts
import { create } from "zustand";

export type SnapPosition =
  | "left"
  | "right"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right"
  | null;

export interface WindowState {
  id: string;
  appId: string;
  position: { x: number; y: number };
  size: { w: number; h: number };
  zIndex: number;
  minimized: boolean;
  maximized: boolean;
  snapped: SnapPosition;
  focused: boolean;
  props?: Record<string, unknown>;
}

interface ProcessStore {
  windows: WindowState[];
  nextZIndex: number;

  openWindow: (
    appId: string,
    defaultSize: { w: number; h: number },
    props?: Record<string, unknown>,
  ) => string;
  closeWindow: (id: string) => void;
  focusWindow: (id: string) => void;
  minimizeWindow: (id: string) => void;
  restoreWindow: (id: string) => void;
  maximizeWindow: (id: string) => void;
  updatePosition: (id: string, x: number, y: number) => void;
  updateSize: (id: string, w: number, h: number) => void;
  snapWindow: (id: string, snap: SnapPosition) => void;
  runningAppIds: () => string[];
}

let idCounter = 0;

export const useProcessStore = create<ProcessStore>((set, get) => ({
  windows: [],
  nextZIndex: 1,

  openWindow(appId, defaultSize, props) {
    const existing = get().windows.find((w) => w.appId === appId);
    if (existing) {
      get().focusWindow(existing.id);
      return existing.id;
    }
    const id = `win-${++idCounter}`;
    const z = get().nextZIndex;
    const offset = (get().windows.length % 8) * 30;
    const win: WindowState = {
      id,
      appId,
      position: { x: 80 + offset, y: 60 + offset },
      size: defaultSize,
      zIndex: z,
      minimized: false,
      maximized: false,
      snapped: null,
      focused: true,
      props,
    };
    set((s) => ({
      windows: s.windows.map((w) => ({ ...w, focused: false })).concat(win),
      nextZIndex: z + 1,
    }));
    return id;
  },

  closeWindow(id) {
    set((s) => ({ windows: s.windows.filter((w) => w.id !== id) }));
  },

  focusWindow(id) {
    const z = get().nextZIndex;
    set((s) => ({
      windows: s.windows.map((w) => ({
        ...w,
        focused: w.id === id,
        zIndex: w.id === id ? z : w.zIndex,
      })),
      nextZIndex: z + 1,
    }));
  },

  minimizeWindow(id) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, minimized: true, focused: false } : w,
      ),
    }));
  },

  restoreWindow(id) {
    const z = get().nextZIndex;
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id
          ? { ...w, minimized: false, focused: true, zIndex: z }
          : { ...w, focused: false },
      ),
      nextZIndex: z + 1,
    }));
  },

  maximizeWindow(id) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, maximized: !w.maximized } : w,
      ),
    }));
  },

  updatePosition(id, x, y) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, position: { x, y } } : w,
      ),
    }));
  },

  updateSize(id, w, h) {
    set((s) => ({
      windows: s.windows.map((win) =>
        win.id === id ? { ...win, size: { w, h } } : win,
      ),
    }));
  },

  snapWindow(id, snap) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, snapped: snap } : w,
      ),
    }));
  },

  runningAppIds() {
    return get().windows.map((w) => w.appId);
  },
}));
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npx vitest run tests/process-store.test.ts 2>&1 | tail -10`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/stores/process-store.ts desktop/tests/process-store.test.ts
git commit -m "add process store: window lifecycle, z-order, snap state"
```

---

## Task 4: Dock Store

**Files:**
- Create: `desktop/src/stores/dock-store.ts`
- Create: `desktop/tests/dock-store.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// desktop/tests/dock-store.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { useDockStore } from "../src/stores/dock-store";

beforeEach(() => {
  useDockStore.setState({
    pinned: ["messages", "agents", "files", "store", "settings"],
  });
});

describe("dock store", () => {
  it("returns default pinned apps", () => {
    expect(useDockStore.getState().pinned).toEqual([
      "messages",
      "agents",
      "files",
      "store",
      "settings",
    ]);
  });

  it("adds a pinned app", () => {
    useDockStore.getState().pin("calculator");
    expect(useDockStore.getState().pinned).toContain("calculator");
  });

  it("does not duplicate a pinned app", () => {
    useDockStore.getState().pin("messages");
    const count = useDockStore
      .getState()
      .pinned.filter((id) => id === "messages").length;
    expect(count).toBe(1);
  });

  it("removes a pinned app", () => {
    useDockStore.getState().unpin("store");
    expect(useDockStore.getState().pinned).not.toContain("store");
  });

  it("reorders pinned apps", () => {
    useDockStore.getState().reorder(["settings", "agents", "messages", "files", "store"]);
    expect(useDockStore.getState().pinned[0]).toBe("settings");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npx vitest run tests/dock-store.test.ts 2>&1 | tail -5`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement dock store**

```typescript
// desktop/src/stores/dock-store.ts
import { create } from "zustand";

const DEFAULT_PINNED = ["messages", "agents", "files", "store", "settings"];

interface DockStore {
  pinned: string[];
  pin: (appId: string) => void;
  unpin: (appId: string) => void;
  reorder: (appIds: string[]) => void;
}

export const useDockStore = create<DockStore>((set, get) => ({
  pinned: DEFAULT_PINNED,

  pin(appId) {
    if (get().pinned.includes(appId)) return;
    set((s) => ({ pinned: [...s.pinned, appId] }));
  },

  unpin(appId) {
    set((s) => ({ pinned: s.pinned.filter((id) => id !== appId) }));
  },

  reorder(appIds) {
    set({ pinned: appIds });
  },
}));
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npx vitest run tests/dock-store.test.ts 2>&1 | tail -5`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/stores/dock-store.ts desktop/tests/dock-store.test.ts
git commit -m "add dock store: pin, unpin, reorder"
```

---

## Task 5: Snap Zone Logic

**Files:**
- Create: `desktop/src/hooks/use-snap-zones.ts`
- Create: `desktop/tests/snap-zones.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// desktop/tests/snap-zones.test.ts
import { describe, it, expect } from "vitest";
import { detectSnapZone, getSnapBounds } from "../src/hooks/use-snap-zones";

const viewport = { width: 1920, height: 1080, topBarH: 32, dockH: 64 };

describe("detectSnapZone", () => {
  it("returns 'left' when dragged to left edge", () => {
    expect(detectSnapZone(5, 400, viewport)).toBe("left");
  });

  it("returns 'right' when dragged to right edge", () => {
    expect(detectSnapZone(1915, 400, viewport)).toBe("right");
  });

  it("returns 'top-left' when dragged to top-left corner", () => {
    expect(detectSnapZone(5, 35, viewport)).toBe("top-left");
  });

  it("returns 'top-right' when dragged to top-right corner", () => {
    expect(detectSnapZone(1915, 35, viewport)).toBe("top-right");
  });

  it("returns 'bottom-left' when dragged to bottom-left corner", () => {
    expect(detectSnapZone(5, 1010, viewport)).toBe("bottom-left");
  });

  it("returns 'bottom-right' when dragged to bottom-right corner", () => {
    expect(detectSnapZone(1915, 1010, viewport)).toBe("bottom-right");
  });

  it("returns null when in the middle of the screen", () => {
    expect(detectSnapZone(960, 540, viewport)).toBeNull();
  });
});

describe("getSnapBounds", () => {
  it("returns left half for 'left' snap", () => {
    const bounds = getSnapBounds("left", viewport);
    expect(bounds).toEqual({ x: 0, y: 32, w: 960, h: 984 });
  });

  it("returns right half for 'right' snap", () => {
    const bounds = getSnapBounds("right", viewport);
    expect(bounds).toEqual({ x: 960, y: 32, w: 960, h: 984 });
  });

  it("returns top-left quarter for 'top-left' snap", () => {
    const bounds = getSnapBounds("top-left", viewport);
    expect(bounds).toEqual({ x: 0, y: 32, w: 960, h: 492 });
  });

  it("returns null for null snap", () => {
    expect(getSnapBounds(null, viewport)).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npx vitest run tests/snap-zones.test.ts 2>&1 | tail -5`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement snap zone logic**

```typescript
// desktop/src/hooks/use-snap-zones.ts
import { useCallback, useState } from "react";
import type { SnapPosition } from "@/stores/process-store";

const EDGE_THRESHOLD = 16; // pixels from edge to trigger snap
const CORNER_SIZE = 100; // pixels from corner to detect quarter snap

interface Viewport {
  width: number;
  height: number;
  topBarH: number;
  dockH: number;
}

export function detectSnapZone(
  x: number,
  y: number,
  vp: Viewport,
): SnapPosition {
  const nearLeft = x <= EDGE_THRESHOLD;
  const nearRight = x >= vp.width - EDGE_THRESHOLD;
  const nearTop = y <= vp.topBarH + CORNER_SIZE;
  const nearBottom = y >= vp.height - vp.dockH - CORNER_SIZE;

  if (nearLeft && nearTop) return "top-left";
  if (nearLeft && nearBottom) return "bottom-left";
  if (nearRight && nearTop) return "top-right";
  if (nearRight && nearBottom) return "bottom-right";
  if (nearLeft) return "left";
  if (nearRight) return "right";
  return null;
}

export function getSnapBounds(
  snap: SnapPosition,
  vp: Viewport,
): { x: number; y: number; w: number; h: number } | null {
  if (!snap) return null;

  const usableH = vp.height - vp.topBarH - vp.dockH;
  const halfW = Math.floor(vp.width / 2);
  const halfH = Math.floor(usableH / 2);

  switch (snap) {
    case "left":
      return { x: 0, y: vp.topBarH, w: halfW, h: usableH };
    case "right":
      return { x: halfW, y: vp.topBarH, w: halfW, h: usableH };
    case "top-left":
      return { x: 0, y: vp.topBarH, w: halfW, h: halfH };
    case "top-right":
      return { x: halfW, y: vp.topBarH, w: halfW, h: halfH };
    case "bottom-left":
      return { x: 0, y: vp.topBarH + halfH, w: halfW, h: halfH };
    case "bottom-right":
      return { x: halfW, y: vp.topBarH + halfH, w: halfW, h: halfH };
  }
}

export function useSnapZones(viewport: Viewport) {
  const [preview, setPreview] = useState<SnapPosition>(null);

  const onDrag = useCallback(
    (x: number, y: number) => {
      setPreview(detectSnapZone(x, y, viewport));
    },
    [viewport],
  );

  const onDragStop = useCallback(() => {
    const result = preview;
    setPreview(null);
    return result;
  }, [preview]);

  return { preview, previewBounds: getSnapBounds(preview, viewport), onDrag, onDragStop };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npx vitest run tests/snap-zones.test.ts 2>&1 | tail -10`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/hooks/use-snap-zones.ts desktop/tests/snap-zones.test.ts
git commit -m "add snap zone detection: edge halves, corner quarters"
```

---

## Task 6: App Registry

**Files:**
- Create: `desktop/src/registry/app-registry.ts`
- Create: `desktop/tests/app-registry.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// desktop/tests/app-registry.test.ts
import { describe, it, expect } from "vitest";
import { getApp, getAppsByCategory, getAllApps } from "../src/registry/app-registry";

describe("app registry", () => {
  it("returns a known app by id", () => {
    const app = getApp("messages");
    expect(app).toBeDefined();
    expect(app!.name).toBe("Messages");
    expect(app!.category).toBe("platform");
  });

  it("returns undefined for unknown app", () => {
    expect(getApp("nonexistent")).toBeUndefined();
  });

  it("filters apps by category", () => {
    const osApps = getAppsByCategory("os");
    expect(osApps.length).toBeGreaterThan(0);
    expect(osApps.every((a) => a.category === "os")).toBe(true);
  });

  it("returns all apps", () => {
    const all = getAllApps();
    expect(all.length).toBeGreaterThan(10);
  });

  it("every app has required fields", () => {
    for (const app of getAllApps()) {
      expect(app.id).toBeTruthy();
      expect(app.name).toBeTruthy();
      expect(app.icon).toBeTruthy();
      expect(app.category).toBeTruthy();
      expect(app.defaultSize).toBeDefined();
      expect(app.minSize).toBeDefined();
    }
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npx vitest run tests/app-registry.test.ts 2>&1 | tail -5`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement app registry**

```typescript
// desktop/src/registry/app-registry.ts
import type { ComponentType } from "react";
import { lazy } from "react";

export interface AppManifest {
  id: string;
  name: string;
  icon: string;
  category: "platform" | "os" | "streaming" | "game";
  component: () => Promise<{ default: ComponentType<{ windowId: string }> }>;
  defaultSize: { w: number; h: number };
  minSize: { w: number; h: number };
  singleton: boolean;
  pinned: boolean;
  launchpadOrder: number;
}

const placeholder = () =>
  import("@/apps/PlaceholderApp").then((m) => ({ default: m.PlaceholderApp }));

const apps: AppManifest[] = [
  // Platform apps
  { id: "messages", name: "Messages", icon: "message-circle", category: "platform", component: placeholder, defaultSize: { w: 900, h: 600 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: true, launchpadOrder: 1 },
  { id: "agents", name: "Agents", icon: "bot", category: "platform", component: placeholder, defaultSize: { w: 1000, h: 650 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: true, launchpadOrder: 2 },
  { id: "files", name: "Files", icon: "folder", category: "platform", component: placeholder, defaultSize: { w: 900, h: 550 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: true, launchpadOrder: 3 },
  { id: "store", name: "Store", icon: "shopping-bag", category: "platform", component: placeholder, defaultSize: { w: 1000, h: 700 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: true, launchpadOrder: 4 },
  { id: "settings", name: "Settings", icon: "settings", category: "platform", component: placeholder, defaultSize: { w: 800, h: 550 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: true, launchpadOrder: 5 },
  { id: "models", name: "Models", icon: "brain", category: "platform", component: placeholder, defaultSize: { w: 900, h: 600 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: false, launchpadOrder: 6 },
  { id: "dashboard", name: "Dashboard", icon: "layout-dashboard", category: "platform", component: placeholder, defaultSize: { w: 1000, h: 650 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 7 },
  { id: "memory", name: "Memory", icon: "database", category: "platform", component: placeholder, defaultSize: { w: 850, h: 550 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 8 },
  { id: "channels", name: "Channels", icon: "radio", category: "platform", component: placeholder, defaultSize: { w: 800, h: 500 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 9 },
  { id: "secrets", name: "Secrets", icon: "key-round", category: "platform", component: placeholder, defaultSize: { w: 750, h: 500 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 10 },
  { id: "tasks", name: "Routines", icon: "calendar-clock", category: "platform", component: placeholder, defaultSize: { w: 800, h: 500 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 11 },
  { id: "import", name: "Import", icon: "upload", category: "platform", component: placeholder, defaultSize: { w: 700, h: 450 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 12 },
  { id: "images", name: "Images", icon: "image", category: "platform", component: placeholder, defaultSize: { w: 900, h: 600 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: false, launchpadOrder: 13 },

  // OS apps
  { id: "calculator", name: "Calculator", icon: "calculator", category: "os", component: placeholder, defaultSize: { w: 320, h: 480 }, minSize: { w: 280, h: 400 }, singleton: true, pinned: false, launchpadOrder: 20 },
  { id: "calendar", name: "Calendar", icon: "calendar", category: "os", component: placeholder, defaultSize: { w: 900, h: 600 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 21 },
  { id: "contacts", name: "Contacts", icon: "contact", category: "os", component: placeholder, defaultSize: { w: 700, h: 500 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 22 },
  { id: "browser", name: "Browser", icon: "globe", category: "os", component: placeholder, defaultSize: { w: 1024, h: 700 }, minSize: { w: 600, h: 400 }, singleton: false, pinned: false, launchpadOrder: 23 },
  { id: "media-player", name: "Media Player", icon: "play-circle", category: "os", component: placeholder, defaultSize: { w: 800, h: 500 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 24 },
  { id: "text-editor", name: "Text Editor", icon: "file-text", category: "os", component: placeholder, defaultSize: { w: 800, h: 550 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 25 },
  { id: "image-viewer", name: "Image Viewer", icon: "eye", category: "os", component: placeholder, defaultSize: { w: 800, h: 600 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 26 },
  { id: "terminal", name: "Terminal", icon: "terminal", category: "os", component: placeholder, defaultSize: { w: 800, h: 500 }, minSize: { w: 400, h: 250 }, singleton: false, pinned: false, launchpadOrder: 27 },

  // Games
  { id: "chess", name: "Chess", icon: "crown", category: "game", component: placeholder, defaultSize: { w: 700, h: 700 }, minSize: { w: 500, h: 500 }, singleton: true, pinned: false, launchpadOrder: 40 },
  { id: "wordle", name: "Wordle", icon: "spell-check", category: "game", component: placeholder, defaultSize: { w: 500, h: 650 }, minSize: { w: 400, h: 550 }, singleton: true, pinned: false, launchpadOrder: 41 },
  { id: "crosswords", name: "Crosswords", icon: "grid-3x3", category: "game", component: placeholder, defaultSize: { w: 700, h: 600 }, minSize: { w: 500, h: 450 }, singleton: true, pinned: false, launchpadOrder: 42 },
];

export function getApp(id: string): AppManifest | undefined {
  return apps.find((a) => a.id === id);
}

export function getAppsByCategory(category: AppManifest["category"]): AppManifest[] {
  return apps.filter((a) => a.category === category);
}

export function getAllApps(): AppManifest[] {
  return [...apps].sort((a, b) => a.launchpadOrder - b.launchpadOrder);
}
```

- [ ] **Step 4: Create PlaceholderApp component**

```tsx
// desktop/src/apps/PlaceholderApp.tsx
export function PlaceholderApp({ windowId }: { windowId: string }) {
  return (
    <div className="flex items-center justify-center h-full text-shell-text-secondary">
      <p>App not yet migrated</p>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd desktop && npx vitest run tests/app-registry.test.ts 2>&1 | tail -10`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add desktop/src/registry/ desktop/src/apps/PlaceholderApp.tsx desktop/tests/app-registry.test.ts
git commit -m "add app registry: 26 apps across platform, os, game categories"
```

---

## Task 7: Window Component

**Files:**
- Create: `desktop/src/components/Window.tsx`
- Create: `desktop/src/components/WindowContent.tsx`
- Create: `desktop/src/components/SnapOverlay.tsx`

- [ ] **Step 1: Create WindowContent**

```tsx
// desktop/src/components/WindowContent.tsx
import { Suspense, lazy, useMemo } from "react";
import { getApp } from "@/registry/app-registry";

interface Props {
  appId: string;
  windowId: string;
}

export function WindowContent({ appId, windowId }: Props) {
  const app = getApp(appId);
  const LazyComponent = useMemo(() => {
    if (!app) return null;
    return lazy(app.component);
  }, [app]);

  if (!LazyComponent) {
    return (
      <div className="flex items-center justify-center h-full text-shell-text-secondary">
        Unknown app: {appId}
      </div>
    );
  }

  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-full text-shell-text-tertiary">
          Loading...
        </div>
      }
    >
      <LazyComponent windowId={windowId} />
    </Suspense>
  );
}
```

- [ ] **Step 2: Create SnapOverlay**

```tsx
// desktop/src/components/SnapOverlay.tsx
interface Props {
  bounds: { x: number; y: number; w: number; h: number } | null;
}

export function SnapOverlay({ bounds }: Props) {
  if (!bounds) return null;

  return (
    <div
      className="fixed pointer-events-none z-[9998] rounded-lg border-2 border-dashed transition-all duration-150"
      style={{
        left: bounds.x,
        top: bounds.y,
        width: bounds.w,
        height: bounds.h,
        backgroundColor: "var(--color-snap-preview)",
        borderColor: "var(--color-snap-border)",
      }}
    />
  );
}
```

- [ ] **Step 3: Create Window component**

```tsx
// desktop/src/components/Window.tsx
import { useCallback, useRef } from "react";
import { Rnd } from "react-rnd";
import { useProcessStore, type WindowState } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { getSnapBounds } from "@/hooks/use-snap-zones";
import { WindowContent } from "./WindowContent";

interface Props {
  win: WindowState;
  onDrag: (x: number, y: number) => void;
  onDragStop: () => import("@/stores/process-store").SnapPosition;
}

export function Window({ win, onDrag, onDragStop }: Props) {
  const { focusWindow, closeWindow, minimizeWindow, maximizeWindow, updatePosition, updateSize, snapWindow } =
    useProcessStore();
  const app = getApp(win.appId);
  const preSnapRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);

  const viewport = {
    width: window.innerWidth,
    height: window.innerHeight,
    topBarH: 32,
    dockH: 64,
  };

  // When maximized or snapped, override position/size
  let displayPos = win.position;
  let displaySize = win.size;

  if (win.maximized) {
    displayPos = { x: 0, y: viewport.topBarH };
    displaySize = { w: viewport.width, h: viewport.height - viewport.topBarH - viewport.dockH };
  } else if (win.snapped) {
    const snapBounds = getSnapBounds(win.snapped, viewport);
    if (snapBounds) {
      displayPos = { x: snapBounds.x, y: snapBounds.y };
      displaySize = { w: snapBounds.w, h: snapBounds.h };
    }
  }

  const handleDragStart = useCallback(() => {
    focusWindow(win.id);
    if (win.snapped) {
      preSnapRef.current = { ...win.position, ...win.size };
      snapWindow(win.id, null);
    }
  }, [focusWindow, snapWindow, win.id, win.snapped, win.position, win.size]);

  const handleDrag = useCallback(
    (_e: unknown, d: { x: number; y: number }) => {
      onDrag(d.x, d.y);
    },
    [onDrag],
  );

  const handleDragStop = useCallback(
    (_e: unknown, d: { x: number; y: number }) => {
      const snap = onDragStop();
      if (snap) {
        preSnapRef.current = { x: d.x, y: d.y, w: win.size.w, h: win.size.h };
        snapWindow(win.id, snap);
      } else {
        updatePosition(win.id, d.x, d.y);
      }
    },
    [onDragStop, snapWindow, updatePosition, win.id, win.size],
  );

  const handleResizeStop = useCallback(
    (_e: unknown, _dir: unknown, ref: HTMLElement) => {
      updateSize(win.id, ref.offsetWidth, ref.offsetHeight);
    },
    [updateSize, win.id],
  );

  if (win.minimized) return null;

  const minSize = app?.minSize ?? { w: 300, h: 200 };

  return (
    <Rnd
      position={{ x: displayPos.x, y: displayPos.y }}
      size={{ width: displaySize.w, height: displaySize.h }}
      minWidth={minSize.w}
      minHeight={minSize.h}
      style={{ zIndex: win.zIndex }}
      dragHandleClassName="window-titlebar"
      disableDragging={win.maximized}
      enableResizing={!win.maximized && !win.snapped}
      onDragStart={handleDragStart}
      onDrag={handleDrag}
      onDragStop={handleDragStop}
      onResizeStop={handleResizeStop}
      onMouseDown={() => focusWindow(win.id)}
      bounds="parent"
    >
      <div
        className={`flex flex-col h-full rounded-[var(--spacing-window-radius)] overflow-hidden border ${
          win.focused
            ? "border-shell-border-strong shadow-[var(--shadow-window)]"
            : "border-shell-border shadow-[var(--shadow-window-unfocused)]"
        }`}
        style={{ backgroundColor: "var(--color-shell-bg)" }}
      >
        {/* Titlebar */}
        <div className="window-titlebar flex items-center h-8 px-3 shrink-0 bg-shell-surface select-none cursor-default">
          {/* Traffic lights */}
          <div className="flex gap-1.5 items-center group">
            <button
              className="w-3 h-3 rounded-full bg-traffic-close hover:brightness-110"
              onClick={(e) => { e.stopPropagation(); closeWindow(win.id); }}
              aria-label="Close window"
            />
            <button
              className="w-3 h-3 rounded-full bg-traffic-minimize hover:brightness-110"
              onClick={(e) => { e.stopPropagation(); minimizeWindow(win.id); }}
              aria-label="Minimize window"
            />
            <button
              className="w-3 h-3 rounded-full bg-traffic-maximize hover:brightness-110"
              onClick={(e) => { e.stopPropagation(); maximizeWindow(win.id); }}
              aria-label="Maximize window"
            />
          </div>
          {/* Title */}
          <div className="flex-1 text-center text-xs text-shell-text-secondary truncate">
            {app?.name ?? win.appId}
          </div>
          {/* Spacer to balance traffic lights */}
          <div className="w-12" />
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto bg-shell-bg-deep">
          <WindowContent appId={win.appId} windowId={win.id} />
        </div>
      </div>
    </Rnd>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add desktop/src/components/Window.tsx desktop/src/components/WindowContent.tsx desktop/src/components/SnapOverlay.tsx
git commit -m "add Window component: traffic lights, drag, resize, snap support"
```

---

## Task 8: Top Bar Component

**Files:**
- Create: `desktop/src/components/TopBar.tsx`
- Create: `desktop/src/hooks/use-clock.ts`

- [ ] **Step 1: Create clock hook**

```typescript
// desktop/src/hooks/use-clock.ts
import { useEffect, useState } from "react";

export function useClock() {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 30_000);
    return () => clearInterval(interval);
  }, []);

  const formatted = time.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  }) + "  " + time.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });

  return formatted;
}
```

- [ ] **Step 2: Create TopBar component**

```tsx
// desktop/src/components/TopBar.tsx
import { Bell, Search } from "lucide-react";
import { useClock } from "@/hooks/use-clock";

interface Props {
  onSearchOpen: () => void;
}

export function TopBar({ onSearchOpen }: Props) {
  const clock = useClock();

  return (
    <div
      className="flex items-center justify-between px-4 shrink-0 select-none"
      style={{
        height: "var(--spacing-topbar-h)",
        backgroundColor: "var(--color-shell-surface)",
        borderBottom: "1px solid var(--color-shell-border)",
      }}
    >
      {/* Left — branding */}
      <div className="flex items-center gap-2">
        <div className="w-4 h-4 rounded bg-accent" />
        <span className="text-xs font-medium text-shell-text-secondary">
          TinyAgentOS
        </span>
      </div>

      {/* Centre — search trigger */}
      <button
        onClick={onSearchOpen}
        className="flex items-center gap-2 px-3 py-1 rounded-md bg-shell-surface-hover text-shell-text-tertiary text-xs hover:bg-shell-surface-active transition-colors"
        aria-label="Search"
      >
        <Search size={12} />
        <span>Search</span>
        <kbd className="ml-2 text-[10px] opacity-50">Ctrl+Space</kbd>
      </button>

      {/* Right — clock + notifications */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-shell-text-tertiary">{clock}</span>
        <button
          className="relative p-1 rounded hover:bg-shell-surface-hover transition-colors"
          aria-label="Notifications"
        >
          <Bell size={14} className="text-shell-text-secondary" />
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop/src/components/TopBar.tsx desktop/src/hooks/use-clock.ts
git commit -m "add TopBar: branding, search trigger, clock, notifications"
```

---

## Task 9: Dock Component

**Files:**
- Create: `desktop/src/components/Dock.tsx`
- Create: `desktop/src/components/DockIcon.tsx`

- [ ] **Step 1: Create DockIcon**

```tsx
// desktop/src/components/DockIcon.tsx
import * as icons from "lucide-react";
import { getApp } from "@/registry/app-registry";

interface Props {
  appId: string;
  isRunning: boolean;
  onClick: () => void;
}

export function DockIcon({ appId, isRunning, onClick }: Props) {
  const app = getApp(appId);
  if (!app) return null;

  // Dynamically get the Lucide icon by name
  const iconName = app.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  const IconComponent = (icons[iconName] as icons.LucideIcon) ?? icons.HelpCircle;

  return (
    <button
      onClick={onClick}
      className="group relative flex items-center justify-center w-10 h-10 rounded-lg bg-shell-surface hover:bg-shell-surface-active transition-all hover:scale-110"
      aria-label={`Open ${app.name}`}
      title={app.name}
    >
      <IconComponent size={20} className="text-shell-text" />
      {/* Running indicator dot */}
      {isRunning && (
        <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-accent" />
      )}
    </button>
  );
}
```

- [ ] **Step 2: Create Dock**

```tsx
// desktop/src/components/Dock.tsx
import { useDockStore } from "@/stores/dock-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { DockIcon } from "./DockIcon";

interface Props {
  onLaunchpadOpen: () => void;
}

export function Dock({ onLaunchpadOpen }: Props) {
  const pinned = useDockStore((s) => s.pinned);
  const windows = useProcessStore((s) => s.windows);
  const { openWindow, focusWindow, restoreWindow } = useProcessStore();
  const runningAppIds = windows.map((w) => w.appId);

  // Running but not pinned
  const runningNotPinned = runningAppIds.filter((id) => !pinned.includes(id));

  const handleClick = (appId: string) => {
    const existing = windows.find((w) => w.appId === appId);
    if (existing) {
      if (existing.minimized) {
        restoreWindow(existing.id);
      } else {
        focusWindow(existing.id);
      }
    } else {
      const app = getApp(appId);
      if (app) {
        openWindow(appId, app.defaultSize);
      }
    }
  };

  return (
    <div
      className="fixed bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 rounded-2xl z-[9999] select-none"
      style={{
        height: "var(--spacing-dock-h)",
        padding: "var(--spacing-dock-padding)",
        backgroundColor: "var(--color-dock-bg)",
        border: "1px solid var(--color-dock-border)",
        boxShadow: "var(--shadow-dock)",
      }}
    >
      {/* Launchpad icon */}
      <button
        onClick={onLaunchpadOpen}
        className="flex items-center justify-center w-10 h-10 rounded-lg bg-shell-surface hover:bg-shell-surface-active transition-all hover:scale-110"
        aria-label="Launchpad"
        title="Launchpad"
      >
        <svg width="18" height="18" viewBox="0 0 16 16" className="text-shell-text" fill="currentColor">
          <rect x="1" y="1" width="5" height="5" rx="1" />
          <rect x="10" y="1" width="5" height="5" rx="1" />
          <rect x="1" y="10" width="5" height="5" rx="1" />
          <rect x="10" y="10" width="5" height="5" rx="1" />
        </svg>
      </button>

      {/* Divider */}
      <div className="w-px h-8 bg-shell-border mx-1" />

      {/* Pinned apps */}
      {pinned.map((appId) => (
        <DockIcon
          key={appId}
          appId={appId}
          isRunning={runningAppIds.includes(appId)}
          onClick={() => handleClick(appId)}
        />
      ))}

      {/* Divider between pinned and running-only */}
      {runningNotPinned.length > 0 && (
        <div className="w-px h-8 bg-shell-border mx-1" />
      )}

      {/* Running apps not in pinned */}
      {runningNotPinned.map((appId) => (
        <DockIcon
          key={appId}
          appId={appId}
          isRunning={true}
          onClick={() => handleClick(appId)}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop/src/components/Dock.tsx desktop/src/components/DockIcon.tsx
git commit -m "add Dock: pinned apps, running indicators, launchpad trigger"
```

---

## Task 10: Launchpad Component

**Files:**
- Create: `desktop/src/components/Launchpad.tsx`
- Create: `desktop/src/components/LaunchpadIcon.tsx`

- [ ] **Step 1: Create LaunchpadIcon**

```tsx
// desktop/src/components/LaunchpadIcon.tsx
import * as icons from "lucide-react";
import type { AppManifest } from "@/registry/app-registry";

interface Props {
  app: AppManifest;
  onClick: () => void;
}

export function LaunchpadIcon({ app, onClick }: Props) {
  const iconName = app.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  const IconComponent = (icons[iconName] as icons.LucideIcon) ?? icons.HelpCircle;

  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center gap-2 p-3 rounded-xl hover:bg-white/5 transition-colors"
      aria-label={`Open ${app.name}`}
    >
      <div className="w-14 h-14 rounded-2xl bg-shell-surface-hover flex items-center justify-center">
        <IconComponent size={28} className="text-shell-text" />
      </div>
      <span className="text-xs text-shell-text-secondary">{app.name}</span>
    </button>
  );
}
```

- [ ] **Step 2: Create Launchpad**

```tsx
// desktop/src/components/Launchpad.tsx
import { useState, useMemo } from "react";
import { Search, X } from "lucide-react";
import { getAllApps, getApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import { LaunchpadIcon } from "./LaunchpadIcon";

interface Props {
  open: boolean;
  onClose: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  platform: "Platform",
  os: "Utilities",
  streaming: "Streaming Apps",
  game: "Games",
};

export function Launchpad({ open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const { openWindow } = useProcessStore();

  const apps = useMemo(() => {
    const all = getAllApps();
    if (!query.trim()) return all;
    const q = query.toLowerCase();
    return all.filter((a) => a.name.toLowerCase().includes(q));
  }, [query]);

  const grouped = useMemo(() => {
    const groups: Record<string, typeof apps> = {};
    for (const app of apps) {
      (groups[app.category] ??= []).push(app);
    }
    return groups;
  }, [apps]);

  const handleLaunch = (appId: string) => {
    const app = getApp(appId);
    if (app) {
      openWindow(appId, app.defaultSize);
    }
    onClose();
    setQuery("");
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[10000] flex flex-col items-center backdrop-blur-md bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl mt-16 px-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search */}
        <div className="flex items-center gap-2 px-4 py-2 mb-8 rounded-xl bg-white/10 border border-white/10">
          <Search size={16} className="text-shell-text-tertiary" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search apps..."
            className="flex-1 bg-transparent text-sm text-shell-text outline-none placeholder:text-shell-text-tertiary"
            autoFocus
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search">
              <X size={14} className="text-shell-text-tertiary" />
            </button>
          )}
        </div>

        {/* App grid by category */}
        <div className="max-h-[60vh] overflow-y-auto space-y-8">
          {Object.entries(grouped).map(([category, categoryApps]) => (
            <div key={category}>
              <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wide mb-3 px-1">
                {CATEGORY_LABELS[category] ?? category}
              </h3>
              <div className="grid grid-cols-6 gap-2">
                {categoryApps.map((app) => (
                  <LaunchpadIcon
                    key={app.id}
                    app={app}
                    onClick={() => handleLaunch(app.id)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop/src/components/Launchpad.tsx desktop/src/components/LaunchpadIcon.tsx
git commit -m "add Launchpad: fullscreen grid with search, categories"
```

---

## Task 11: Desktop Surface + Assemble Shell

**Files:**
- Create: `desktop/src/components/Desktop.tsx`
- Modify: `desktop/src/App.tsx`
- Create: `desktop/src/hooks/use-keyboard-shortcuts.ts`

- [ ] **Step 1: Create keyboard shortcuts hook**

```typescript
// desktop/src/hooks/use-keyboard-shortcuts.ts
import { useEffect } from "react";

interface ShortcutHandlers {
  onSearch: () => void;
  onLaunchpad: () => void;
}

export function useKeyboardShortcuts({ onSearch, onLaunchpad }: ShortcutHandlers) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey;

      if (ctrl && e.code === "Space") {
        e.preventDefault();
        onSearch();
      }
      if (ctrl && e.key === "l") {
        e.preventDefault();
        onLaunchpad();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onSearch, onLaunchpad]);
}
```

- [ ] **Step 2: Create Desktop surface**

```tsx
// desktop/src/components/Desktop.tsx
import { useProcessStore } from "@/stores/process-store";
import { useSnapZones } from "@/hooks/use-snap-zones";
import { Window } from "./Window";
import { SnapOverlay } from "./SnapOverlay";

export function Desktop() {
  const windows = useProcessStore((s) => s.windows);

  const viewport = {
    width: typeof window !== "undefined" ? window.innerWidth : 1920,
    height: typeof window !== "undefined" ? window.innerHeight : 1080,
    topBarH: 32,
    dockH: 64,
  };

  const { preview, previewBounds, onDrag, onDragStop } = useSnapZones(viewport);

  return (
    <div
      className="relative flex-1 overflow-hidden"
      style={{
        background: "linear-gradient(160deg, #1a1b2e 0%, #1e2140 40%, #252848 100%)",
      }}
    >
      <SnapOverlay bounds={previewBounds} />
      {windows.map((win) => (
        <Window
          key={win.id}
          win={win}
          onDrag={onDrag}
          onDragStop={onDragStop}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Assemble App.tsx**

```tsx
// desktop/src/App.tsx
import { useState, useCallback } from "react";
import { TopBar } from "@/components/TopBar";
import { Desktop } from "@/components/Desktop";
import { Dock } from "@/components/Dock";
import { Launchpad } from "@/components/Launchpad";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";

export function App() {
  const [launchpadOpen, setLaunchpadOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  const toggleLaunchpad = useCallback(() => setLaunchpadOpen((v) => !v), []);
  const toggleSearch = useCallback(() => setSearchOpen((v) => !v), []);

  useKeyboardShortcuts({
    onSearch: toggleSearch,
    onLaunchpad: toggleLaunchpad,
  });

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-shell-bg text-shell-text">
      <TopBar onSearchOpen={toggleSearch} />
      <Desktop />
      <Dock onLaunchpadOpen={toggleLaunchpad} />
      <Launchpad open={launchpadOpen} onClose={() => setLaunchpadOpen(false)} />
    </div>
  );
}
```

- [ ] **Step 4: Verify the shell renders in the browser**

Run:
```bash
cd desktop && npm run dev -- --port 5173 &
sleep 3 && curl -s http://localhost:5173 | grep -c "root"
kill %1
```
Expected: `1` (the page renders). Open `http://localhost:5173` in a browser — you should see the top bar, desktop gradient, and dock with 5 pinned app icons. Click an icon to open a window with "App not yet migrated" placeholder. Drag windows, snap to edges, minimize, maximize, close.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/
git commit -m "assemble desktop shell: top bar, desktop, dock, launchpad, window manager"
```

---

## Task 12: Backend — Desktop Settings Store + Routes

**Files:**
- Create: `tinyagentos/desktop_settings.py`
- Create: `tinyagentos/routes/desktop.py`
- Modify: `tinyagentos/app.py` (add router + SPA serving)
- Create: `tests/test_desktop_settings.py`

- [ ] **Step 1: Write failing tests for the store**

```python
# tests/test_desktop_settings.py
import pytest
from pathlib import Path
from tinyagentos.desktop_settings import DesktopSettingsStore


@pytest.fixture
async def store(tmp_path):
    s = DesktopSettingsStore(tmp_path / "desktop.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_get_default_settings(store):
    settings = await store.get_settings("user")
    assert settings["mode"] == "dark"
    assert settings["wallpaper"] == "default"
    assert "pinned" in settings["dock"]


@pytest.mark.asyncio
async def test_update_settings(store):
    await store.update_settings("user", {"mode": "light"})
    settings = await store.get_settings("user")
    assert settings["mode"] == "light"


@pytest.mark.asyncio
async def test_get_dock_layout(store):
    dock = await store.get_dock("user")
    assert isinstance(dock["pinned"], list)
    assert "messages" in dock["pinned"]


@pytest.mark.asyncio
async def test_update_dock_layout(store):
    await store.update_dock("user", {"pinned": ["agents", "files"]})
    dock = await store.get_dock("user")
    assert dock["pinned"] == ["agents", "files"]


@pytest.mark.asyncio
async def test_window_positions_roundtrip(store):
    positions = [{"appId": "messages", "x": 100, "y": 200, "w": 900, "h": 600}]
    await store.save_windows("user", positions)
    result = await store.get_windows("user")
    assert result == positions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_desktop_settings.py -v 2>&1 | tail -10`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement DesktopSettingsStore**

```python
# tinyagentos/desktop_settings.py
from __future__ import annotations
import json
from pathlib import Path
from tinyagentos.base_store import BaseStore

DEFAULT_SETTINGS = {
    "mode": "dark",
    "accentColor": "#667eea",
    "wallpaper": "default",
    "dockMagnification": False,
    "topBarOpacity": 0.95,
}

DEFAULT_DOCK = {
    "pinned": ["messages", "agents", "files", "store", "settings"],
}


class DesktopSettingsStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS desktop_settings (
        user_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (user_id, key)
    );
    """

    async def _get(self, user_id: str, key: str, default: dict) -> dict:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM desktop_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        row = await cursor.fetchone()
        if row:
            saved = json.loads(row[0])
            merged = {**default, **saved}
            return merged
        return dict(default)

    async def _set(self, user_id: str, key: str, value: dict) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO desktop_settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, json.dumps(value)),
        )
        await self._db.commit()

    async def get_settings(self, user_id: str) -> dict:
        return await self._get(user_id, "settings", DEFAULT_SETTINGS)

    async def update_settings(self, user_id: str, updates: dict) -> None:
        current = await self.get_settings(user_id)
        current.update(updates)
        await self._set(user_id, "settings", current)

    async def get_dock(self, user_id: str) -> dict:
        return await self._get(user_id, "dock", DEFAULT_DOCK)

    async def update_dock(self, user_id: str, updates: dict) -> None:
        current = await self.get_dock(user_id)
        current.update(updates)
        await self._set(user_id, "dock", current)

    async def get_windows(self, user_id: str) -> list:
        data = await self._get(user_id, "windows", {"positions": []})
        return data.get("positions", [])

    async def save_windows(self, user_id: str, positions: list) -> None:
        await self._set(user_id, "windows", {"positions": positions})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_desktop_settings.py -v 2>&1 | tail -10`
Expected: All 5 tests PASS.

- [ ] **Step 5: Create desktop routes**

```python
# tinyagentos/routes/desktop.py
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

router = APIRouter()

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SPA_DIR = PROJECT_DIR / "static" / "desktop"


@router.get("/api/desktop/settings")
async def get_settings(request: Request):
    store = request.app.state.desktop_settings
    settings = await store.get_settings("user")
    return JSONResponse(settings)


@router.put("/api/desktop/settings")
async def update_settings(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.update_settings("user", body)
    return JSONResponse({"ok": True})


@router.get("/api/desktop/dock")
async def get_dock(request: Request):
    store = request.app.state.desktop_settings
    dock = await store.get_dock("user")
    return JSONResponse(dock)


@router.put("/api/desktop/dock")
async def update_dock(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.update_dock("user", body)
    return JSONResponse({"ok": True})


@router.get("/api/desktop/windows")
async def get_windows(request: Request):
    store = request.app.state.desktop_settings
    windows = await store.get_windows("user")
    return JSONResponse(windows)


@router.put("/api/desktop/windows")
async def save_windows(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.save_windows("user", body.get("positions", []))
    return JSONResponse({"ok": True})


@router.get("/desktop/{rest:path}")
async def serve_spa(rest: str = ""):
    """Serve the React SPA. All /desktop/* routes serve index.html (client-side routing)."""
    index = SPA_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"error": "Desktop shell not built. Run: cd desktop && npm run build"}, status_code=404)
```

- [ ] **Step 6: Wire into app.py**

Add these lines in `tinyagentos/app.py` after the existing router imports (after line 353):

```python
from tinyagentos.routes.desktop import router as desktop_router
app.include_router(desktop_router)
```

And initialise the store in the startup section (after the existing store inits):

```python
from tinyagentos.desktop_settings import DesktopSettingsStore
desktop_store = DesktopSettingsStore(data_dir / "desktop.db")
await desktop_store.init()
app.state.desktop_settings = desktop_store
```

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/desktop_settings.py tinyagentos/routes/desktop.py tests/test_desktop_settings.py
git commit -m "add desktop settings store and API routes"
```

---

## Task 13: Build + Serve Integration

**Files:**
- Modify: `desktop/vite.config.ts` (already set, verify)
- Modify: `tinyagentos/app.py` (mount SPA static assets)

- [ ] **Step 1: Build the desktop shell**

Run:
```bash
cd /home/jay/tinyagentos/desktop && npm run build 2>&1 | tail -10
```
Expected: Build succeeds, output in `../static/desktop/`.

- [ ] **Step 2: Verify built files exist**

Run:
```bash
ls /home/jay/tinyagentos/static/desktop/
```
Expected: `index.html`, `assets/` directory with JS and CSS bundles.

- [ ] **Step 3: Mount SPA static assets in app.py**

Add after the existing static mount (after line 265 in `app.py`):

```python
spa_dir = PROJECT_DIR / "static" / "desktop"
if spa_dir.exists():
    app.mount("/desktop/assets", StaticFiles(directory=str(spa_dir / "assets")), name="desktop-assets")
```

- [ ] **Step 4: Test end-to-end — start FastAPI, access /desktop**

Run:
```bash
cd /home/jay/tinyagentos && python -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port 6969 &
sleep 3 && curl -s http://localhost:6969/desktop | head -5
kill %1
```
Expected: HTML response containing `<div id="root">` and script tags pointing to `/desktop/assets/`.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/app.py static/desktop/
git commit -m "integrate desktop shell build: serve SPA at /desktop"
```

---

## Task 14: Run Full Test Suite

- [ ] **Step 1: Run frontend tests**

Run:
```bash
cd /home/jay/tinyagentos/desktop && npx vitest run 2>&1 | tail -15
```
Expected: All tests PASS (process store, dock store, snap zones, app registry).

- [ ] **Step 2: Run backend tests**

Run:
```bash
cd /home/jay/tinyagentos && python -m pytest tests/test_desktop_settings.py -v 2>&1 | tail -10
```
Expected: All 5 tests PASS.

- [ ] **Step 3: Run existing test suite to check for regressions**

Run:
```bash
cd /home/jay/tinyagentos && python -m pytest tests/ -v --ignore=tests/e2e 2>&1 | tail -20
```
Expected: All existing tests still PASS. No regressions from the new routes or store.

- [ ] **Step 4: Commit any test fixes if needed**

---

Plan complete and saved to `docs/design/plan-desktop-shell-core.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?