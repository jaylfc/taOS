# Shell Cross-App Drag-Drop Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended).

**Goal:** Land the shell drag-drop primitive (hybrid in-memory bus + HTML5 mirror), then wire FilesApp as a source and MessagesApp composer/attachments bar as a target. Further source/target wiring (Messages→TextEditor, Library, Canvas) follow in subsequent tasks.

**Architecture:** Singleton event-emitter bus + two React hooks (`useDragSource`, `useDropTarget`). Native HTML5 drag for cross-origin/OS drags, rich typed payloads for in-app.

**Tech Stack:** React + TypeScript + Vitest + Playwright.

---

## File structure

### New
- `desktop/src/shell/dnd/types.ts` — `DragPayload` union + `DragKind`.
- `desktop/src/shell/dnd/dnd-bus.ts` — singleton state + pub/sub.
- `desktop/src/shell/dnd/use-drag-source.ts` — hook.
- `desktop/src/shell/dnd/use-drop-target.ts` — hook.
- `desktop/src/shell/dnd/__tests__/dnd-bus.test.ts`
- `desktop/src/shell/dnd/__tests__/use-drag-source.test.tsx`
- `desktop/src/shell/dnd/__tests__/use-drop-target.test.tsx`
- `tests/e2e/test_cross_app_dnd.py`

### Modified
- `desktop/src/apps/FilesApp.tsx` — file rows as drag sources.
- `desktop/src/apps/MessagesApp.tsx` — composer + attachments bar as drop targets.
- `desktop/src/apps/chat/MessageHoverActions.tsx` — add message drag handle.
- `desktop/src/apps/LibraryApp.tsx` — knowledge rows as drag sources.
- `desktop/src/apps/CanvasApp.tsx` (if canvas edits are in scope) — blocks as sources, canvas surface as target.
- `desktop/src/apps/TextEditorApp.tsx` — drop target.

---

## Task 1: Shell DnD primitives (bus + hooks + types)

**Files:**
- Create: `desktop/src/shell/dnd/types.ts`
- Create: `desktop/src/shell/dnd/dnd-bus.ts`
- Create: `desktop/src/shell/dnd/use-drag-source.ts`
- Create: `desktop/src/shell/dnd/use-drop-target.ts`
- Create: `desktop/src/shell/dnd/__tests__/dnd-bus.test.ts`
- Create: `desktop/src/shell/dnd/__tests__/use-drag-source.test.tsx`
- Create: `desktop/src/shell/dnd/__tests__/use-drop-target.test.tsx`

### Step 1: Types

`desktop/src/shell/dnd/types.ts`:

```typescript
export type DragPayload =
  | { kind: "file"; path: string; mime_type: string; size: number; name: string }
  | { kind: "message"; channel_id: string; message_id: string; author_id: string; excerpt: string }
  | { kind: "knowledge"; id: string; title: string; url?: string }
  | { kind: "canvas-block"; canvas_id: string; block_id: string; block_type: string };

export type DragKind = DragPayload["kind"];
```

### Step 2: Bus tests

`desktop/src/shell/dnd/__tests__/dnd-bus.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { startDrag, endDrag, getCurrent, subscribe } from "../dnd-bus";

const samplePayload = { kind: "file" as const, path: "/a/b.png", mime_type: "image/png", size: 10, name: "b.png" };

describe("dnd-bus", () => {
  beforeEach(() => {
    endDrag(); // reset state
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    endDrag();
  });

  it("startDrag sets current and notifies subscribers", () => {
    const fn = vi.fn();
    const unsub = subscribe(fn);
    startDrag(samplePayload);
    expect(getCurrent()).toEqual(samplePayload);
    expect(fn).toHaveBeenCalled();
    unsub();
  });

  it("endDrag clears current", () => {
    startDrag(samplePayload);
    endDrag();
    expect(getCurrent()).toBeNull();
  });

  it("30s stale timeout auto-clears", () => {
    startDrag(samplePayload);
    expect(getCurrent()).not.toBeNull();
    vi.advanceTimersByTime(30_000);
    expect(getCurrent()).toBeNull();
  });

  it("starting a new drag resets the stale timer", () => {
    startDrag(samplePayload);
    vi.advanceTimersByTime(25_000);
    startDrag({ ...samplePayload, name: "c.png" });
    vi.advanceTimersByTime(15_000); // 15s after restart
    expect(getCurrent()).not.toBeNull();
    vi.advanceTimersByTime(20_000); // 35s after restart total
    expect(getCurrent()).toBeNull();
  });

  it("subscribers receive change events for both start and end", () => {
    const fn = vi.fn();
    subscribe(fn);
    startDrag(samplePayload);
    endDrag();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
```

### Step 3: Run tests → FAIL (module missing)

Run: `cd desktop && npm test -- --run dnd-bus`

### Step 4: Implement `dnd-bus.ts`

```typescript
import type { DragPayload } from "./types";

let current: DragPayload | null = null;
const emitter = new EventTarget();
let staleTimer: ReturnType<typeof setTimeout> | null = null;

const STALE_MS = 30_000;

function emit() {
  emitter.dispatchEvent(new Event("change"));
}

export function startDrag(payload: DragPayload): void {
  current = payload;
  if (staleTimer) clearTimeout(staleTimer);
  staleTimer = setTimeout(() => {
    current = null;
    staleTimer = null;
    emit();
  }, STALE_MS);
  emit();
}

export function endDrag(): void {
  if (current === null && staleTimer === null) return;
  current = null;
  if (staleTimer) {
    clearTimeout(staleTimer);
    staleTimer = null;
  }
  emit();
}

export function getCurrent(): DragPayload | null {
  return current;
}

export function subscribe(listener: () => void): () => void {
  emitter.addEventListener("change", listener);
  return () => emitter.removeEventListener("change", listener);
}
```

### Step 5: Run tests → 5 pass

### Step 6: `use-drag-source` tests

`desktop/src/shell/dnd/__tests__/use-drag-source.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDragSource } from "../use-drag-source";
import { getCurrent, endDrag } from "../dnd-bus";

describe("useDragSource", () => {
  beforeEach(() => endDrag());

  it("onDragStart calls startDrag on the bus", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload }));
    const setData = vi.fn();
    const e = { dataTransfer: { setData, effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(getCurrent()).toEqual(payload);
  });

  it("htmlMirror writes each mime via dataTransfer.setData", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({
      payload,
      htmlMirror: { "text/plain": "/a.txt", "text/uri-list": "https://h/a.txt" },
    }));
    const setData = vi.fn();
    const e = { dataTransfer: { setData, effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(setData).toHaveBeenCalledWith("text/plain", "/a.txt");
    expect(setData).toHaveBeenCalledWith("text/uri-list", "https://h/a.txt");
  });

  it("disabled=true sets draggable=false", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload, disabled: true }));
    expect(result.current.dragHandlers.draggable).toBe(false);
  });

  it("onDragEnd clears the bus", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload }));
    const e = { dataTransfer: { setData: vi.fn(), effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(getCurrent()).not.toBeNull();
    act(() => { result.current.dragHandlers.onDragEnd(); });
    expect(getCurrent()).toBeNull();
  });
});
```

### Step 7: Run tests → FAIL

### Step 8: Implement `use-drag-source.ts`

```typescript
import type { DragPayload } from "./types";
import { startDrag, endDrag } from "./dnd-bus";

export interface UseDragSourceOpts<T extends DragPayload> {
  payload: T;
  disabled?: boolean;
  htmlMirror?: Record<string, string>;
}

export function useDragSource<T extends DragPayload>(opts: UseDragSourceOpts<T>) {
  const { payload, disabled = false, htmlMirror } = opts;
  return {
    dragHandlers: {
      draggable: !disabled,
      onDragStart: (e: React.DragEvent) => {
        if (disabled) return;
        try {
          e.dataTransfer.effectAllowed = "copy";
          if (htmlMirror) {
            for (const [mime, value] of Object.entries(htmlMirror)) {
              try { e.dataTransfer.setData(mime, value); } catch { /* best-effort */ }
            }
          }
        } catch { /* ignore */ }
        startDrag(payload);
      },
      onDragEnd: () => {
        endDrag();
      },
    },
  };
}
```

### Step 9: Run tests → 4 pass

### Step 10: `use-drop-target` tests

`desktop/src/shell/dnd/__tests__/use-drop-target.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDropTarget } from "../use-drop-target";
import { startDrag, endDrag } from "../dnd-bus";

const filePayload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
const msgPayload = { kind: "message" as const, channel_id: "c1", message_id: "m1", author_id: "tom", excerpt: "hi" };

describe("useDropTarget", () => {
  beforeEach(() => endDrag());

  it("isValidTarget true when bus payload matches accept", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    expect(result.current.isValidTarget).toBe(false);
    act(() => { startDrag(filePayload); });
    rerender();
    expect(result.current.isValidTarget).toBe(true);
  });

  it("isValidTarget false when bus payload type not accepted", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    act(() => { startDrag(msgPayload); });
    rerender();
    expect(result.current.isValidTarget).toBe(false);
  });

  it("onDrop callback fires with payload when valid", () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop }),
    );
    act(() => { startDrag(filePayload); });
    const e = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDrop(e); });
    expect(e.preventDefault).toHaveBeenCalled();
    expect(onDrop).toHaveBeenCalledWith(filePayload);
  });

  it("onDrop callback does NOT fire when invalid type", () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop }),
    );
    act(() => { startDrag(msgPayload); });
    const e = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDrop(e); });
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("isOver tracks enter/leave counter for nested children", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    const enter = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDragEnter(enter); });
    rerender();
    expect(result.current.isOver).toBe(true);
    // simulate enter on a nested child (double enter before leave)
    act(() => { result.current.dropHandlers.onDragEnter(enter); });
    act(() => { result.current.dropHandlers.onDragLeave(enter); });
    rerender();
    expect(result.current.isOver).toBe(true); // still over (parent still holds)
    act(() => { result.current.dropHandlers.onDragLeave(enter); });
    rerender();
    expect(result.current.isOver).toBe(false);
  });
});
```

### Step 11: Run tests → FAIL

### Step 12: Implement `use-drop-target.ts`

```typescript
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { DragKind, DragPayload } from "./types";
import { getCurrent, subscribe } from "./dnd-bus";

export interface UseDropTargetOpts {
  accept: DragKind[];
  onDrop: (payload: DragPayload) => void | Promise<void>;
  disabled?: boolean;
}

export function useDropTarget(opts: UseDropTargetOpts) {
  const { accept, onDrop, disabled = false } = opts;
  const current = useSyncExternalStore(subscribe, getCurrent, () => null);
  const enterCounter = useRef(0);
  const [isOver, setIsOver] = useState(false);

  const isValidTarget = !disabled && current !== null && accept.includes(current.kind);

  useEffect(() => {
    if (current === null) {
      enterCounter.current = 0;
      setIsOver(false);
    }
  }, [current]);

  return {
    isOver,
    isValidTarget,
    dropHandlers: {
      onDragEnter: (e: React.DragEvent) => {
        if (disabled) return;
        e.preventDefault();
        enterCounter.current += 1;
        if (enterCounter.current === 1) setIsOver(true);
      },
      onDragOver: (e: React.DragEvent) => {
        if (disabled) return;
        e.preventDefault();
      },
      onDragLeave: (_e: React.DragEvent) => {
        if (disabled) return;
        enterCounter.current = Math.max(0, enterCounter.current - 1);
        if (enterCounter.current === 0) setIsOver(false);
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        enterCounter.current = 0;
        setIsOver(false);
        const payload = getCurrent();
        if (!disabled && payload && accept.includes(payload.kind)) {
          try {
            const r = onDrop(payload);
            if (r && typeof (r as Promise<void>).then === "function") {
              (r as Promise<void>).catch((err) => console.warn("drop handler failed", err));
            }
          } catch (err) {
            console.warn("drop handler failed", err);
          }
        }
      },
    },
  };
}
```

### Step 13: Run all 3 test files → 14 pass

```bash
cd desktop && npm test -- --run dnd
```

### Step 14: Build

```bash
cd desktop && npm run build
```
Expected: clean.

### Step 15: Commit

```bash
git add desktop/src/shell/dnd
git commit -m "feat(shell): cross-app DnD primitives — bus, useDragSource, useDropTarget"
```

---

## Task 2: FilesApp source + MessagesApp target

**Files:**
- Modify: `desktop/src/apps/FilesApp.tsx` — add `useDragSource` on file rows.
- Modify: `desktop/src/apps/MessagesApp.tsx` — add `useDropTarget` on composer + attachments bar; handle file-kind payload.

### Step 1: FilesApp source

Locate the file row render (grep for `draggable` or `onClick={() => handleRowClick(` in `FilesApp.tsx`). For each file row `<button>` / `<div>`, spread drag handlers:

```tsx
// Near top of FilesApp.tsx
import { useDragSource } from "@/shell/dnd/use-drag-source";

// Inside the row component or inline when mapping:
const { dragHandlers } = useDragSource({
  payload: {
    kind: "file",
    path: fullPath,  // e.g. "/workspaces/user/foo.pdf"
    mime_type: entry.mime_type ?? guessFromExt(entry.name),
    size: entry.size ?? 0,
    name: entry.name,
  },
  htmlMirror: { "text/plain": fullPath },
  disabled: entry.is_dir, // don't drag folders
});

<div {...dragHandlers} ...>
```

If the file listing component re-renders heavily, put the hook inside a stable `FileRow` subcomponent to avoid re-creating handlers.

### Step 2: MessagesApp target — composer

In `desktop/src/apps/MessagesApp.tsx`, replace the existing `onDragOver`/`onDrop` on the message list div with `useDropTarget`. Add:

```tsx
import { useDropTarget } from "@/shell/dnd/use-drop-target";

// Inside component, near other hooks:
const filesDropTarget = useDropTarget({
  accept: ["file"],
  onDrop: async (payload) => {
    if (payload.kind !== "file") return;
    // Convert VFS path into a workspace attachment via existing API.
    const id = Math.random().toString(36).slice(2);
    setPendingAttachments((p) => [...p, {
      id, filename: payload.name, size: payload.size, uploading: true,
    }]);
    try {
      const rec = await attachmentFromPath({
        path: payload.path,
        source: payload.path.startsWith("/workspaces/agent/") ? "agent-workspace" : "workspace",
        slug: payload.path.startsWith("/workspaces/agent/")
          ? payload.path.split("/")[3]
          : undefined,
      });
      setPendingAttachments((p) =>
        p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x)
      );
    } catch (e) {
      setPendingAttachments((p) =>
        p.map((x) => x.id === id ? { ...x, uploading: false, error: (e as Error).message } : x)
      );
    }
  },
});
```

Apply `filesDropTarget.dropHandlers` to the message list div (replacing the previous inline `onDragOver`/`onDrop`). Keep the existing native-file drop handler separately for OS file drags — actually, merge them: if `e.dataTransfer.files.length > 0`, use the OS path; else use `getCurrent()` from the bus.

Simpler: leave the OS file-drop path untouched (existing `onDragOver`/`onDrop` native path). Just ADD the shell bus handlers via a combined spread. Merge pattern:

```tsx
<div
  ref={messageListRef}
  onScroll={handleScroll}
  className="flex-1 overflow-y-auto px-4 py-3 space-y-0.5"
  onDragOver={(e) => {
    filesDropTarget.dropHandlers.onDragOver(e);
    e.preventDefault(); // redundant with above, harmless
  }}
  onDragEnter={filesDropTarget.dropHandlers.onDragEnter}
  onDragLeave={filesDropTarget.dropHandlers.onDragLeave}
  onDrop={(e) => {
    // Native OS file drops have dataTransfer.files
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      e.preventDefault();
      for (const f of Array.from(e.dataTransfer.files)) {
        // existing handler body unchanged
        /* ... */
      }
      return;
    }
    // Shell bus drops
    filesDropTarget.dropHandlers.onDrop(e);
  }}
  style={isMobile && keyboardInset > 0 ? { paddingBottom: `${keyboardInset + 60}px` } : undefined}
>
```

Also add visual ring when `isValidTarget`:

```tsx
className={`flex-1 overflow-y-auto px-4 py-3 space-y-0.5 ${
  filesDropTarget.isOver ? "bg-sky-500/5 ring-2 ring-sky-400/60 ring-inset" :
  filesDropTarget.isValidTarget ? "ring-2 ring-sky-400/30 ring-inset" : ""
}`}
```

### Step 3: Build + tests

```bash
cd desktop && npm run build && npm test -- --run
```
Expected: clean; no new failures.

### Step 4: Commit

```bash
git add desktop/src/apps/FilesApp.tsx desktop/src/apps/MessagesApp.tsx
git commit -m "feat(shell): files → messages drag-drop via shell DnD primitive"
```

---

## Task 3: Rebuild bundle

```bash
cd desktop && npm run build
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for shell DnD primitive"
```

---

## Task 4: Playwright E2E

**File:** `tests/e2e/test_cross_app_dnd.py`

```python
"""Shell cross-app drag-drop E2E — Files → Messages.

Requires TAOS_E2E_URL set and at least one file in the user workspace.
"""
import os
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TAOS_E2E_URL"),
        reason="TAOS_E2E_URL required",
    ),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_drag_file_from_files_to_messages_composer(page: Page):
    page.goto(URL)
    # Open Files + Messages side by side (window manager spec)
    page.get_by_role("button", name="Files").click()
    file_row = page.locator("[data-file-row]").first
    # Open Messages in another window
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    composer_drop_target = page.locator(".message-list-drop-target").first
    # HTML5 drag simulation
    file_row.drag_to(composer_drop_target)
    # Assert attachment chip appears in the pending bar
    expect(page.get_by_text(re.compile(r"\.(txt|md|png|jpg|pdf)", re.I))).to_be_visible()
```

Note: `data-file-row` and `.message-list-drop-target` will need to be added as test hooks in the two apps. If the existing DOM doesn't have them, add them in Task 2's integration (not a new task). Playwright's `drag_to` fires HTML5 drag events natively.

Commit:
```bash
git add tests/e2e/test_cross_app_dnd.py
git commit -m "test(e2e): cross-app DnD — drag file from Files into Messages"
```

---

## Final verification

- `cd desktop && npm test -- --run` — all new unit tests pass.
- `cd desktop && npm run build` — clean.
- `PYTHONPATH=. pytest tests/ -x -q --ignore=tests/e2e` — no regressions.

```bash
git push -u origin feat/shell-cross-app-dnd
gh pr create --base master \
  --title "Shell: cross-app drag-drop primitive + Files → Messages wiring" \
  --body-file docs/superpowers/specs/2026-04-20-shell-cross-app-dnd-design.md
```

## Follow-ups (not in this PR)

- Messages source + TextEditor drop target (quote-into-notes).
- Library source wiring.
- Canvas source + target.
- Invalid-target visual (red ring on `isDragging && !isValidTarget` to signal "I won't accept this").
- Touch DnD (long-press) on mobile.
