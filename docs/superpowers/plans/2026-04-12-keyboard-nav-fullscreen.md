# Keyboard Navigation & Fullscreen Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a centralized shortcut registry with system shortcuts, fullscreen launch with keyboard lock, and in-app keyboard navigation hooks.

**Architecture:** `ShortcutProvider` React context at the app root with a single keydown listener, priority dispatch (overlay > app > system), `useShortcut` hook for registration. Login page with fullscreen + keyboard lock launch. Shared `useFocusTrap` and `useListNav` hooks for consistent in-app keyboard nav.

**Tech Stack:** React, TypeScript, Tailwind, Vitest, lucide-react

**Spec:** `docs/superpowers/specs/2026-04-12-keyboard-nav-fullscreen-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `desktop/src/hooks/use-shortcut-registry.tsx` | ShortcutProvider context + useShortcut + useShortcuts hooks |
| Create | `desktop/tests/shortcut-registry.test.ts` | Unit tests for registry logic |
| Delete | `desktop/src/hooks/use-keyboard-shortcuts.ts` | Replaced by registry |
| Modify | `desktop/src/App.tsx` | Wrap in ShortcutProvider, register system shortcuts, remove old hook |
| Modify | `desktop/src/components/SearchPalette.tsx` | Register via useShortcut, auto-select first result |
| Modify | `desktop/src/components/Launchpad.tsx` | Register Escape via useShortcut |
| Create | `desktop/src/hooks/use-focus-trap.ts` | Focus trapping hook for modals/overlays |
| Create | `desktop/src/hooks/use-list-nav.ts` | Arrow key list navigation hook |
| Create | `desktop/tests/focus-trap.test.ts` | Tests for focus trap logic |
| Create | `desktop/tests/list-nav.test.ts` | Tests for list nav logic |
| Create | `desktop/src/components/LoginScreen.tsx` | Fullscreen launch page |
| Modify | `desktop/src/App.tsx` | Login gate with fullscreen flow |

---

## Task 1: Shortcut Registry

**Files:**
- Create: `desktop/src/hooks/use-shortcut-registry.tsx`
- Create: `desktop/tests/shortcut-registry.test.ts`

- [ ] **Step 1: Write failing tests**

Create `desktop/tests/shortcut-registry.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { parseCombo, matchesEvent, ShortcutEntry } from "../src/hooks/use-shortcut-registry";

describe("parseCombo", () => {
  it("parses simple key", () => {
    expect(parseCombo("Escape")).toEqual({ ctrl: false, shift: false, alt: false, key: "escape" });
  });

  it("parses Ctrl+key", () => {
    expect(parseCombo("Ctrl+W")).toEqual({ ctrl: true, shift: false, alt: false, key: "w" });
  });

  it("parses Ctrl+Shift+key", () => {
    expect(parseCombo("Ctrl+Shift+Tab")).toEqual({ ctrl: true, shift: true, alt: false, key: "tab" });
  });

  it("normalises order", () => {
    expect(parseCombo("Shift+Ctrl+A")).toEqual(parseCombo("Ctrl+Shift+A"));
  });
});

describe("matchesEvent", () => {
  function fakeEvent(key: string, ctrl = false, shift = false, alt = false, meta = false): KeyboardEvent {
    return { key, ctrlKey: ctrl, shiftKey: shift, altKey: alt, metaKey: meta } as KeyboardEvent;
  }

  it("matches Ctrl+W", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w", true))).toBe(true);
  });

  it("treats Meta as Ctrl", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w", false, false, false, true))).toBe(true);
  });

  it("does not match without modifier", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w"))).toBe(false);
  });

  it("matches Escape with no modifiers", () => {
    const parsed = parseCombo("Escape");
    expect(matchesEvent(parsed, fakeEvent("Escape"))).toBe(true);
  });

  it("does not match Escape when Ctrl is pressed", () => {
    const parsed = parseCombo("Escape");
    expect(matchesEvent(parsed, fakeEvent("Escape", true))).toBe(false);
  });

  it("matches Ctrl+Shift+Tab", () => {
    const parsed = parseCombo("Ctrl+Shift+Tab");
    expect(matchesEvent(parsed, fakeEvent("Tab", true, true))).toBe(true);
  });
});

describe("priority", () => {
  it("overlay beats system", () => {
    const entries: ShortcutEntry[] = [
      { id: "sys", combo: parseCombo("Escape"), action: vi.fn(), label: "System", scope: "system", enabled: true },
      { id: "ovl", combo: parseCombo("Escape"), action: vi.fn(), label: "Overlay", scope: "overlay", enabled: true },
    ];
    // Overlay should be first when sorted by priority
    const sorted = [...entries].sort((a, b) => {
      const order = { overlay: 0, app: 1, system: 2 };
      return order[a.scope] - order[b.scope];
    });
    expect(sorted[0].id).toBe("ovl");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/shortcut-registry.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the shortcut registry**

Create `desktop/src/hooks/use-shortcut-registry.tsx`:

```tsx
import { createContext, useContext, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/* ------------------------------------------------------------------ */
/*  Combo parsing                                                      */
/* ------------------------------------------------------------------ */

export interface ParsedCombo {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  key: string; // lowercase
}

export function parseCombo(combo: string): ParsedCombo {
  const parts = combo.toLowerCase().split("+").map((p) => p.trim());
  return {
    ctrl: parts.includes("ctrl"),
    shift: parts.includes("shift"),
    alt: parts.includes("alt"),
    key: parts.filter((p) => !["ctrl", "shift", "alt"].includes(p))[0] ?? "",
  };
}

export function matchesEvent(parsed: ParsedCombo, e: KeyboardEvent): boolean {
  const ctrl = e.ctrlKey || e.metaKey; // Treat Meta (Cmd) as Ctrl
  if (parsed.ctrl !== ctrl) return false;
  if (parsed.shift !== e.shiftKey) return false;
  if (parsed.alt !== e.altKey) return false;
  return e.key.toLowerCase() === parsed.key;
}

/* ------------------------------------------------------------------ */
/*  Registry types                                                     */
/* ------------------------------------------------------------------ */

export type ShortcutScope = "system" | "app" | "overlay";

export interface ShortcutEntry {
  id: string;
  combo: ParsedCombo;
  action: () => void;
  label: string;
  scope: ShortcutScope;
  enabled: boolean;
}

interface ShortcutRegistryContext {
  register: (id: string, combo: string, action: () => void, label: string, scope?: ShortcutScope) => void;
  unregister: (id: string) => void;
  getAll: () => { combo: string; label: string; scope: ShortcutScope }[];
  keyboardLockActive: boolean;
}

const Ctx = createContext<ShortcutRegistryContext | null>(null);

/* ------------------------------------------------------------------ */
/*  Provider                                                           */
/* ------------------------------------------------------------------ */

const SCOPE_PRIORITY: Record<ShortcutScope, number> = { overlay: 0, app: 1, system: 2 };

export function ShortcutProvider({ children }: { children: ReactNode }) {
  const entriesRef = useRef<Map<string, ShortcutEntry>>(new Map());
  const [keyboardLockActive, setKeyboardLockActive] = useState(false);

  // Try to lock keyboard when entering fullscreen
  useEffect(() => {
    const onFullscreenChange = () => {
      if (document.fullscreenElement && navigator.keyboard?.lock) {
        navigator.keyboard.lock().then(() => setKeyboardLockActive(true)).catch(() => setKeyboardLockActive(false));
      } else {
        setKeyboardLockActive(false);
      }
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  // Single keydown listener
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const entries = Array.from(entriesRef.current.values())
        .filter((s) => s.enabled)
        .sort((a, b) => SCOPE_PRIORITY[a.scope] - SCOPE_PRIORITY[b.scope]);

      for (const entry of entries) {
        if (matchesEvent(entry.combo, e)) {
          e.preventDefault();
          e.stopPropagation();
          entry.action();
          return;
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const register = useCallback((id: string, combo: string, action: () => void, label: string, scope: ShortcutScope = "system") => {
    entriesRef.current.set(id, { id, combo: parseCombo(combo), action, label, scope, enabled: true });
  }, []);

  const unregister = useCallback((id: string) => {
    entriesRef.current.delete(id);
  }, []);

  const getAll = useCallback(() => {
    return Array.from(entriesRef.current.values()).map((e) => ({
      combo: formatCombo(e.combo),
      label: e.label,
      scope: e.scope,
    }));
  }, []);

  return <Ctx.Provider value={{ register, unregister, getAll, keyboardLockActive }}>{children}</Ctx.Provider>;
}

function formatCombo(parsed: ParsedCombo): string {
  const parts: string[] = [];
  if (parsed.ctrl) parts.push("Ctrl");
  if (parsed.shift) parts.push("Shift");
  if (parsed.alt) parts.push("Alt");
  parts.push(parsed.key.charAt(0).toUpperCase() + parsed.key.slice(1));
  return parts.join("+");
}

/* ------------------------------------------------------------------ */
/*  Hooks                                                              */
/* ------------------------------------------------------------------ */

export function useShortcut(combo: string, action: () => void, label: string, scope: ShortcutScope = "system") {
  const ctx = useContext(Ctx);
  const idRef = useRef(`shortcut-${combo}-${Math.random().toString(36).slice(2, 8)}`);

  useEffect(() => {
    ctx?.register(idRef.current, combo, action, label, scope);
    return () => ctx?.unregister(idRef.current);
  }, [ctx, combo, action, label, scope]);
}

export function useShortcuts() {
  const ctx = useContext(Ctx);
  return {
    getAll: ctx?.getAll ?? (() => []),
    keyboardLockActive: ctx?.keyboardLockActive ?? false,
  };
}
```

- [ ] **Step 4: Run tests**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/shortcut-registry.test.ts`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add desktop/src/hooks/use-shortcut-registry.tsx desktop/tests/shortcut-registry.test.ts
git commit -m "feat(keyboard): add ShortcutProvider registry with combo parsing and priority dispatch"
```

---

## Task 2: Wire System Shortcuts + Remove Old Hook

**Files:**
- Modify: `desktop/src/App.tsx`
- Delete: `desktop/src/hooks/use-keyboard-shortcuts.ts`
- Modify: `desktop/src/components/SearchPalette.tsx`
- Modify: `desktop/src/components/Launchpad.tsx`

- [ ] **Step 1: Read existing files**

Read `App.tsx`, `use-keyboard-shortcuts.ts`, `SearchPalette.tsx`, `Launchpad.tsx` to understand current wiring.

- [ ] **Step 2: Wrap App in ShortcutProvider**

In `App.tsx`:
- Import `ShortcutProvider` from `@/hooks/use-shortcut-registry`
- Wrap the root JSX in `<ShortcutProvider>...</ShortcutProvider>`
- Remove the `useKeyboardShortcuts()` call
- Remove the import of `use-keyboard-shortcuts`

- [ ] **Step 3: Register system shortcuts in App.tsx**

Add a `SystemShortcuts` component inside the provider that registers all system shortcuts using `useShortcut`:

```tsx
import { useShortcut } from "@/hooks/use-shortcut-registry";

function SystemShortcuts() {
  const { openWindow, closeWindow, minimizeWindow, maximizeWindow, cycleWindow } = useProcessStore();
  const [searchOpen, setSearchOpen] = useState(false);
  const [launchpadOpen, setLaunchpadOpen] = useState(false);

  useShortcut("Ctrl+Space", () => setSearchOpen((v) => !v), "Toggle search palette", "system");
  useShortcut("Ctrl+L", () => setLaunchpadOpen((v) => !v), "Toggle launchpad", "system");
  useShortcut("Ctrl+W", () => { /* close focused window */ }, "Close window", "system");
  useShortcut("Ctrl+M", () => { /* minimize focused window */ }, "Minimize window", "system");
  useShortcut("Ctrl+F", () => { /* maximize focused window */ }, "Maximize/restore window", "system");
  useShortcut("Ctrl+Tab", () => { /* cycle next window */ }, "Next window", "system");
  useShortcut("Ctrl+Shift+Tab", () => { /* cycle previous window */ }, "Previous window", "system");
  // Ctrl+1-9 for dock apps
  for (let i = 1; i <= 9; i++) {
    useShortcut(`Ctrl+${i}`, () => { /* open/focus dock app i */ }, `Open dock app ${i}`, "system");
  }

  return null;
}
```

The actual implementations read from the process store (for window management) and dock store (for Ctrl+1-9 mapping). Read those stores to get the correct method calls.

- [ ] **Step 4: Update SearchPalette**

In `SearchPalette.tsx`:
- Register `Escape` as an overlay shortcut via `useShortcut("Escape", onClose, "Close search", "overlay")` when the palette is open
- Auto-select first result: when `results` changes, set `selectedIndex` to `0`
- Remove any existing `document.addEventListener("keydown")` for Escape if present

- [ ] **Step 5: Update Launchpad**

In `Launchpad.tsx`:
- Register `Escape` as overlay shortcut via `useShortcut("Escape", onClose, "Close launchpad", "overlay")` when open

- [ ] **Step 6: Delete old hook**

Delete `desktop/src/hooks/use-keyboard-shortcuts.ts`.

- [ ] **Step 7: Build + test**

Run: `cd /home/jay/tinyagentos/desktop && npx tsc --noEmit && npm test`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(keyboard): wire system shortcuts via registry, remove legacy hook"
```

---

## Task 3: Focus Trap + List Nav Hooks

**Files:**
- Create: `desktop/src/hooks/use-focus-trap.ts`
- Create: `desktop/src/hooks/use-list-nav.ts`
- Create: `desktop/tests/focus-trap.test.ts`
- Create: `desktop/tests/list-nav.test.ts`

- [ ] **Step 1: Write focus trap tests**

```ts
import { describe, it, expect } from "vitest";
import { getFocusableElements } from "../src/hooks/use-focus-trap";

describe("getFocusableElements", () => {
  it("returns empty array for null ref", () => {
    expect(getFocusableElements(null)).toEqual([]);
  });
});
```

- [ ] **Step 2: Implement useFocusTrap**

```ts
import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function getFocusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active || !ref.current) return;

    previousFocus.current = document.activeElement as HTMLElement;
    const focusable = getFocusableElements(ref.current);
    if (focusable.length > 0) focusable[0].focus();

    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const elements = getFocusableElements(ref.current);
      if (elements.length === 0) return;

      const first = elements[0];
      const last = elements[elements.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    ref.current.addEventListener("keydown", handler);
    const el = ref.current;
    return () => {
      el.removeEventListener("keydown", handler);
      previousFocus.current?.focus();
    };
  }, [ref, active]);
}
```

- [ ] **Step 3: Write list nav tests**

```ts
import { describe, it, expect } from "vitest";
import { computeNextIndex } from "../src/hooks/use-list-nav";

describe("computeNextIndex", () => {
  it("moves down", () => {
    expect(computeNextIndex(0, 5, "ArrowDown")).toBe(1);
  });
  it("moves up", () => {
    expect(computeNextIndex(1, 5, "ArrowUp")).toBe(0);
  });
  it("wraps down", () => {
    expect(computeNextIndex(4, 5, "ArrowDown")).toBe(0);
  });
  it("wraps up", () => {
    expect(computeNextIndex(0, 5, "ArrowUp")).toBe(4);
  });
  it("Home goes to 0", () => {
    expect(computeNextIndex(3, 5, "Home")).toBe(0);
  });
  it("End goes to last", () => {
    expect(computeNextIndex(1, 5, "End")).toBe(4);
  });
});
```

- [ ] **Step 4: Implement useListNav**

```ts
import { useState, useCallback, type KeyboardEvent } from "react";

export function computeNextIndex(current: number, total: number, key: string): number {
  if (total === 0) return -1;
  if (key === "ArrowDown") return (current + 1) % total;
  if (key === "ArrowUp") return (current - 1 + total) % total;
  if (key === "Home") return 0;
  if (key === "End") return total - 1;
  return current;
}

export function useListNav<T>(items: T[], onSelect: (item: T) => void) {
  const [selectedIndex, setSelectedIndex] = useState(0);

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        setSelectedIndex((prev) => computeNextIndex(prev, items.length, e.key));
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (items[selectedIndex]) onSelect(items[selectedIndex]);
      }
    },
    [items, selectedIndex, onSelect],
  );

  return { selectedIndex, setSelectedIndex, onKeyDown };
}
```

- [ ] **Step 5: Run tests**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/focus-trap.test.ts tests/list-nav.test.ts`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add desktop/src/hooks/use-focus-trap.ts desktop/src/hooks/use-list-nav.ts desktop/tests/focus-trap.test.ts desktop/tests/list-nav.test.ts
git commit -m "feat(keyboard): add useFocusTrap and useListNav hooks"
```

---

## Task 4: Fullscreen Launch Screen

**Files:**
- Create: `desktop/src/components/LoginScreen.tsx`
- Modify: `desktop/src/App.tsx`

- [ ] **Step 1: Create LoginScreen component**

```tsx
import { useState, useEffect } from "react";
import { Button } from "@/components/ui";

function getBrowserTier(): "full" | "safari" | "limited" {
  const ua = navigator.userAgent;
  if (/Chrome|Chromium|Edg|OPR/i.test(ua) && !/Safari/i.test(ua)) return "full";
  if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) return "safari";
  return "limited";
}

export function LoginScreen({ onLaunch }: { onLaunch: () => void }) {
  const [launching, setLaunching] = useState(false);
  const tier = getBrowserTier();

  const handleLaunch = async () => {
    setLaunching(true);
    try {
      await document.documentElement.requestFullscreen();
    } catch { /* fullscreen may not be available */ }
    // Short delay for warp animation
    setTimeout(onLaunch, 600);
  };

  return (
    <div className={`fixed inset-0 z-[9999] bg-black flex items-center justify-center transition-all duration-600 ${launching ? "scale-115 opacity-0" : "scale-100 opacity-100"}`}>
      <div className="text-center space-y-8">
        <img src="/static/taos-logo.png" alt="taOS" className="w-64 mx-auto" />
        <Button
          size="lg"
          onClick={handleLaunch}
          className="text-lg px-8 py-4"
          aria-label="Launch taOS"
        >
          Launch taOS
        </Button>
        {tier === "safari" && (
          <p className="text-sm text-shell-text-tertiary">Install taOS as an app for the best experience</p>
        )}
        {tier === "limited" && (
          <p className="text-sm text-shell-text-tertiary">For full keyboard support, use Chrome or Edge</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire into App.tsx**

Add a `launched` state to App.tsx. Show `LoginScreen` when not launched. On launch, animate in the desktop with a fade/scale transition.

Add a "Return to fullscreen" pill when `document.fullscreenElement` is null but `launched` is true.

Register `Ctrl+Q` system shortcut that shows a quit confirmation, then calls `keyboard.unlock()` + `document.exitFullscreen()` + sets `launched = false`.

- [ ] **Step 3: Build + test**

Run: `cd /home/jay/tinyagentos/desktop && npx tsc --noEmit && npm test && npm run build`

- [ ] **Step 4: Commit**

```bash
git add desktop/src/components/LoginScreen.tsx desktop/src/App.tsx
git commit -m "feat(fullscreen): add login screen with fullscreen launch and keyboard lock"
```

---

## Task 5: Apply Hooks to Existing Apps

**Files:**
- Modify: `desktop/src/components/SearchPalette.tsx` — auto-select first result
- Modify: `desktop/src/apps/LibraryApp.tsx` — useFocusTrap on category manager modal
- Modify: `desktop/src/components/Launchpad.tsx` — useListNav for app grid

- [ ] **Step 1: SearchPalette auto-select**

When results change, set `selectedIndex = 0` so Enter immediately launches the top result.

- [ ] **Step 2: LibraryApp focus trap**

Import `useFocusTrap`, add ref to category manager modal div, activate when `showCategoryManager` is true.

- [ ] **Step 3: Launchpad list nav**

Import `useListNav`, wire to the app grid for arrow key navigation. Enter launches the selected app.

- [ ] **Step 4: Build + test**

Run: `cd /home/jay/tinyagentos/desktop && npx tsc --noEmit && npm test && npm run build`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(keyboard): apply focus trap and list nav to SearchPalette, Library, and Launchpad"
```

---

## TDD Summary

| Task | Tests | What it delivers |
|------|-------|------------------|
| 1 | 9 vitest | Shortcut registry with combo parsing and priority |
| 2 | Build check | System shortcuts wired, old hook removed |
| 3 | 7 vitest | useFocusTrap + useListNav hooks |
| 4 | Build check | Login screen with fullscreen launch |
| 5 | Build check | Hooks applied to existing apps |

**Acceptance criteria:** All tests pass. System shortcuts work (Ctrl+Space, Ctrl+L, Ctrl+W, Ctrl+1-9, Ctrl+Tab, Escape). Login screen launches fullscreen with keyboard lock on Chrome/Edge. SearchPalette auto-selects first result. Category manager traps focus. Launchpad navigable with arrow keys.
