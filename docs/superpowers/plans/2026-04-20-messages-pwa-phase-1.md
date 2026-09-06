# Messages PWA Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the mobile PWA experience for taOS talk — full-screen thread takeover, bottom sheet overflow menu, keyboard-aware composer, install prompt.

**Architecture:** Additive mobile behaviors gated on `useIsMobile()`. New shell primitives (`BottomSheet`, `InstallPromptBanner`) + new hook (`use-visual-viewport`). ThreadPanel gains an `isFullscreen` prop. No backend changes.

**Tech Stack:** React + TypeScript + Tailwind + Vitest (desktop); Playwright mobile-viewport E2E.

---

## File structure

### New
- `desktop/src/hooks/use-visual-viewport.ts`
- `desktop/src/hooks/__tests__/use-visual-viewport.test.ts`
- `desktop/src/shell/BottomSheet.tsx`
- `desktop/src/shell/__tests__/BottomSheet.test.tsx`
- `desktop/src/shell/InstallPromptBanner.tsx`
- `desktop/src/shell/__tests__/InstallPromptBanner.test.tsx`
- `tests/e2e/test_messages_pwa.py`

### Modified
- `desktop/src/apps/chat/ThreadPanel.tsx` — add `isFullscreen` prop.
- `desktop/src/apps/MessagesApp.tsx` — mobile conditionals: thread takeover, bottom-sheet overflow, keyboard-aware composer.
- `desktop/src/ChatStandalone.tsx` — mount `<InstallPromptBanner>`.
- `desktop/chat.html` — iOS splash meta tags.

---

## Task 1: `use-visual-viewport` hook

**Files:**
- Create: `desktop/src/hooks/use-visual-viewport.ts`
- Create: `desktop/src/hooks/__tests__/use-visual-viewport.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// desktop/src/hooks/__tests__/use-visual-viewport.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useVisualViewport } from "../use-visual-viewport";

describe("useVisualViewport", () => {
  const listeners = new Map<string, Set<() => void>>();
  let vv: { height: number; offsetTop: number; addEventListener: (t: string, l: () => void) => void; removeEventListener: (t: string, l: () => void) => void };

  beforeEach(() => {
    listeners.clear();
    vv = {
      height: 800,
      offsetTop: 0,
      addEventListener: (t, l) => {
        if (!listeners.has(t)) listeners.set(t, new Set());
        listeners.get(t)!.add(l);
      },
      removeEventListener: (t, l) => listeners.get(t)?.delete(l),
    };
    Object.defineProperty(window, "visualViewport", { value: vv, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
  });

  afterEach(() => {
    // @ts-expect-error test cleanup
    delete window.visualViewport;
  });

  it("returns height + keyboardInset=0 when viewport matches window", () => {
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current.height).toBe(800);
    expect(result.current.keyboardInset).toBe(0);
  });

  it("computes keyboardInset when viewport shrinks (keyboard open)", () => {
    const { result } = renderHook(() => useVisualViewport());
    act(() => {
      vv.height = 500;
      listeners.get("resize")?.forEach((l) => l());
    });
    expect(result.current.keyboardInset).toBe(300);
  });

  it("returns fallback when visualViewport is undefined", () => {
    // @ts-expect-error delete VV
    delete window.visualViewport;
    Object.defineProperty(window, "innerHeight", { value: 600, configurable: true });
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current.height).toBe(600);
    expect(result.current.keyboardInset).toBe(0);
  });
});
```

- [ ] **Step 2: Run test**

Run: `cd desktop && npm test -- --run use-visual-viewport`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```typescript
// desktop/src/hooks/use-visual-viewport.ts
import { useEffect, useState } from "react";

export interface VisualViewportState {
  height: number;
  keyboardInset: number;
}

function read(): VisualViewportState {
  if (typeof window === "undefined") return { height: 0, keyboardInset: 0 };
  const vv = window.visualViewport;
  if (!vv) return { height: window.innerHeight, keyboardInset: 0 };
  const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
  return { height: vv.height, keyboardInset: inset };
}

export function useVisualViewport(): VisualViewportState {
  const [state, setState] = useState<VisualViewportState>(read);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => setState(read());
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);

  return state;
}
```

- [ ] **Step 4: Run test**

Run: `cd desktop && npm test -- --run use-visual-viewport`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/hooks/use-visual-viewport.ts desktop/src/hooks/__tests__/use-visual-viewport.test.ts
git commit -m "feat(desktop): use-visual-viewport hook for keyboard-aware mobile UI"
```

---

## Task 2: `BottomSheet` shell primitive

**Files:**
- Create: `desktop/src/shell/BottomSheet.tsx`
- Create: `desktop/src/shell/__tests__/BottomSheet.test.tsx`

- [ ] **Step 1: Write failing tests**

```tsx
// desktop/src/shell/__tests__/BottomSheet.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BottomSheet } from "../BottomSheet";

describe("BottomSheet", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <BottomSheet open={false} onClose={vi.fn()}>
        <div>content</div>
      </BottomSheet>
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders children when open", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()}>
        <div>hello sheet</div>
      </BottomSheet>
    );
    expect(screen.getByText("hello sheet")).toBeInTheDocument();
  });

  it("Escape key calls onClose", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open={true} onClose={onClose}>
        <div>x</div>
      </BottomSheet>
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("backdrop click calls onClose", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open={true} onClose={onClose}>
        <div>x</div>
      </BottomSheet>
    );
    fireEvent.click(screen.getByTestId("bottom-sheet-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  it("drag handle renders by default", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()}>
        <div>x</div>
      </BottomSheet>
    );
    expect(screen.getByTestId("bottom-sheet-handle")).toBeInTheDocument();
  });

  it("dragHandle=false hides the handle", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()} dragHandle={false}>
        <div>x</div>
      </BottomSheet>
    );
    expect(screen.queryByTestId("bottom-sheet-handle")).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd desktop && npm test -- --run BottomSheet`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// desktop/src/shell/BottomSheet.tsx
import { useEffect, useRef, useState } from "react";

export interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  labelledBy?: string;
  dragHandle?: boolean;
}

const DISMISS_THRESHOLD_PX = 80;

export function BottomSheet({
  open, onClose, children, labelledBy, dragHandle = true,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const [dragY, setDragY] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    // Focus first focusable element when opening
    if (!open) return;
    const first = sheetRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    first?.focus();
  }, [open]);

  if (!open) return null;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const startY = e.clientY;
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const onMove = (ev: PointerEvent) => {
      const dy = Math.max(0, ev.clientY - startY);
      setDragY(dy);
    };
    const onUp = (ev: PointerEvent) => {
      const dy = Math.max(0, ev.clientY - startY);
      el.releasePointerCapture(e.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      if (dy > DISMISS_THRESHOLD_PX) onClose();
      setDragY(0);
    };
    const onCancel = () => {
      el.releasePointerCapture(e.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      setDragY(0);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
  };

  return (
    <>
      <div
        data-testid="bottom-sheet-backdrop"
        className="fixed inset-0 z-50 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className="fixed bottom-0 inset-x-0 z-50 bg-shell-surface rounded-t-xl border-t border-white/10 shadow-2xl max-h-[85vh] overflow-y-auto"
        style={{
          paddingBottom: "env(safe-area-inset-bottom, 0px)",
          transform: `translateY(${dragY}px)`,
          transition: dragY === 0 ? "transform 0.2s ease-out" : "none",
        }}
      >
        {dragHandle && (
          <div
            data-testid="bottom-sheet-handle"
            onPointerDown={onPointerDown}
            className="flex justify-center py-2 cursor-grab active:cursor-grabbing touch-none"
          >
            <div className="w-10 h-1 bg-white/20 rounded-full" />
          </div>
        )}
        {children}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run BottomSheet`
Expected: 6 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/shell/BottomSheet.tsx desktop/src/shell/__tests__/BottomSheet.test.tsx
git commit -m "feat(desktop): BottomSheet shell primitive with drag-to-dismiss"
```

---

## Task 3: `InstallPromptBanner`

**Files:**
- Create: `desktop/src/shell/InstallPromptBanner.tsx`
- Create: `desktop/src/shell/__tests__/InstallPromptBanner.test.tsx`

- [ ] **Step 1: Write failing tests**

```tsx
// desktop/src/shell/__tests__/InstallPromptBanner.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { InstallPromptBanner } from "../InstallPromptBanner";

const fireBeforeInstallPrompt = (prompt: () => Promise<{outcome: string}>) => {
  const event = new Event("beforeinstallprompt") as Event & {
    prompt: () => Promise<{outcome: string}>;
    userChoice: Promise<{outcome: string}>;
  };
  // @ts-expect-error test wiring
  event.prompt = prompt;
  // @ts-expect-error test wiring
  event.userChoice = Promise.resolve({ outcome: "accepted" });
  window.dispatchEvent(event);
};

describe("InstallPromptBanner", () => {
  beforeEach(() => {
    localStorage.clear();
    // fake mobile + not standalone
    Object.defineProperty(window, "matchMedia", {
      value: (q: string) => ({
        matches: q.includes("max-width") ? true : false,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
      configurable: true,
    });
    Object.defineProperty(window, "innerWidth", { value: 400, configurable: true });
  });

  it("renders nothing until beforeinstallprompt fires", () => {
    const { container } = render(<InstallPromptBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders after beforeinstallprompt and install click calls prompt()", async () => {
    const promptSpy = vi.fn(() => Promise.resolve({ outcome: "accepted" }));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(promptSpy);
    });
    expect(screen.getByRole("region", { name: /install/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /install/i }));
    expect(promptSpy).toHaveBeenCalled();
  });

  it("Not now click writes dismissal timestamp and hides", async () => {
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    fireEvent.click(screen.getByRole("button", { name: /not now/i }));
    expect(localStorage.getItem("taos-install-dismissed")).not.toBeNull();
    expect(screen.queryByRole("region", { name: /install/i })).toBeNull();
  });

  it("stays hidden when recently dismissed", async () => {
    localStorage.setItem("taos-install-dismissed", String(Date.now()));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    expect(screen.queryByRole("region", { name: /install/i })).toBeNull();
  });

  it("reappears after 30 days", async () => {
    const thirtyOneDaysAgo = Date.now() - 31 * 24 * 60 * 60 * 1000;
    localStorage.setItem("taos-install-dismissed", String(thirtyOneDaysAgo));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    expect(screen.getByRole("region", { name: /install/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd desktop && npm test -- --run InstallPromptBanner`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// desktop/src/shell/InstallPromptBanner.tsx
import { useEffect, useState } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<{ outcome: "accepted" | "dismissed" }>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_MS = 30 * 24 * 60 * 60 * 1000;
const KEY = "taos-install-dismissed";

export function InstallPromptBanner() {
  const isMobile = useIsMobile();
  const [event, setEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setEvent(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  if (!isMobile || !event || dismissed) return null;

  // Already installed?
  if (typeof window !== "undefined") {
    const mql = window.matchMedia("(display-mode: standalone)");
    if (mql.matches) return null;
  }

  // Recently dismissed?
  const prev = localStorage.getItem(KEY);
  if (prev && Date.now() - Number(prev) < DISMISS_MS) return null;

  const install = async () => {
    try {
      await event.prompt();
      await event.userChoice;
    } catch {
      /* ignore */
    }
    setEvent(null);
  };

  const notNow = () => {
    localStorage.setItem(KEY, String(Date.now()));
    setDismissed(true);
  };

  return (
    <div
      role="region"
      aria-label="Install prompt"
      className="flex items-center gap-3 px-4 py-2 bg-sky-500/20 border-b border-sky-500/30 text-sm"
    >
      <span className="flex-1">Install taOS talk for quick access</span>
      <button
        onClick={install}
        className="px-3 py-1 bg-sky-500/40 text-sky-100 rounded hover:bg-sky-500/60"
      >Install</button>
      <button
        onClick={notNow}
        className="px-2 py-1 opacity-70 hover:opacity-100"
      >Not now</button>
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run InstallPromptBanner`
Expected: 5 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/shell/InstallPromptBanner.tsx desktop/src/shell/__tests__/InstallPromptBanner.test.tsx
git commit -m "feat(desktop): InstallPromptBanner with 30-day dismissal suppression"
```

---

## Task 4: ThreadPanel `isFullscreen` prop

**Files:**
- Modify: `desktop/src/apps/chat/ThreadPanel.tsx`

Add an `isFullscreen?: boolean` prop. When true, render as full-screen takeover instead of right-side slide-over.

- [ ] **Step 1: Update the component**

In `ThreadPanel.tsx`, change the signature:

```tsx
export function ThreadPanel({
  channelId,
  parentId,
  onClose,
  onSend,
  isFullscreen = false,
}: {
  channelId: string;
  parentId: string;
  onClose: () => void;
  onSend: (content: string, attachments: AttachmentRecord[]) => Promise<void>;
  isFullscreen?: boolean;
}) {
```

Swap the outer `<div>` className so that when `isFullscreen`, the panel is full-screen instead of right-side:

```tsx
<div
  className={
    isFullscreen
      ? "fixed inset-0 z-50 bg-shell-surface flex flex-col"
      : "fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 flex flex-col z-40"
  }
  role="complementary"
  aria-label="Thread panel"
  style={isFullscreen ? { paddingTop: "env(safe-area-inset-top, 0px)" } : undefined}
>
```

Update the close button's aria-label on fullscreen mode — render "◀" with aria-label="Back" when fullscreen, keep "✕" / "Close thread" when side-panel:

```tsx
<button
  aria-label={isFullscreen ? "Back" : "Close thread"}
  onClick={onClose}
  className="p-1 hover:bg-white/5 rounded"
>{isFullscreen ? "◀" : "✕"}</button>
```

- [ ] **Step 2: Build passes**

Run: `cd desktop && npm run build`
Expected: clean.

- [ ] **Step 3: Run existing tests**

Run: `cd desktop && npm test -- --run ThreadPanel`
Expected: pass (no tests for ThreadPanel — just don't break anything else).

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/chat/ThreadPanel.tsx
git commit -m "feat(desktop): ThreadPanel isFullscreen prop for mobile takeover"
```

---

## Task 5: MessagesApp — mobile thread takeover

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

- [ ] **Step 1: Pass `isFullscreen` when mobile**

Find the `<ThreadPanel` mount (around line 1718 — search for `<ThreadPanel`). Add `isFullscreen={isMobile}`:

```tsx
{openThread && (
  <ThreadPanel
    channelId={openThread.channelId}
    parentId={openThread.parentId}
    onClose={closeThread}
    isFullscreen={isMobile}
    onSend={async (content, attachments) => { /* unchanged */ }}
  />
)}
```

- [ ] **Step 2: Build + full test**

Run: `cd desktop && npm run build && npm test -- --run`
Expected: build clean, no new test failures.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): mobile thread takeover when viewport is < 768px"
```

---

## Task 6: MessagesApp — overflow menu bottom-sheet

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

- [ ] **Step 1: Add import**

Near the other imports:

```tsx
import { BottomSheet } from "@/shell/BottomSheet";
```

- [ ] **Step 2: Split overflow menu render by isMobile**

Find the overflow menu mount (search for `overflowMenu && (() =>`). Replace the current block with:

```tsx
{overflowMenu && (() => {
  const msg = messages.find((m) => m.id === overflowMenu.messageId);
  if (!msg) return null;
  const menu = (
    <MessageOverflowMenu
      isOwn={msg.author_id === currentUserId}
      isHuman={true}
      isPinned={pinnedMessages.some((p) => p.id === msg.id)}
      onEdit={() => handleEdit(msg.id)}
      onDelete={() => handleDelete(msg.id)}
      onCopyLink={() => handleCopyLink(msg.id)}
      onPin={() => handlePin(msg)}
      onMarkUnread={() => handleMarkUnread(msg.id)}
      onClose={() => setOverflowMenu(null)}
    />
  );
  if (isMobile) {
    return (
      <BottomSheet open={true} onClose={() => setOverflowMenu(null)}>
        {menu}
      </BottomSheet>
    );
  }
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={() => setOverflowMenu(null)} />
      <div className="fixed z-50" style={{ top: overflowMenu.y, left: overflowMenu.x }}>
        {menu}
      </div>
    </>
  );
})()}
```

- [ ] **Step 3: Build + test**

Run: `cd desktop && npm run build && npm test -- --run`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): mobile overflow menu renders in bottom sheet"
```

---

## Task 7: MessagesApp — keyboard-aware composer

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

- [ ] **Step 1: Add import + state**

Near the other imports:

```tsx
import { useVisualViewport } from "@/hooks/use-visual-viewport";
```

Inside the component, near other hooks:

```tsx
const { keyboardInset } = useVisualViewport();
```

- [ ] **Step 2: Apply to composer + message list**

Find the composer outer div (the one with `px-4 py-3 border-t ...` containing the Textarea + Send button). Add an inline style that only applies on mobile:

```tsx
<div
  className="px-4 py-3 border-t border-white/[0.06] shrink-0"
  style={
    isMobile
      ? { paddingBottom: `max(env(safe-area-inset-bottom), ${keyboardInset}px)` }
      : undefined
  }
>
```

Find the message list scroll container (`<div ref={messageListRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 py-3 space-y-0.5">`). Add padding-bottom so messages don't scroll under the composer when the keyboard is up:

```tsx
<div
  ref={messageListRef}
  onScroll={handleScroll}
  className="flex-1 overflow-y-auto px-4 py-3 space-y-0.5"
  style={isMobile && keyboardInset > 0 ? { paddingBottom: `${keyboardInset + 60}px` } : undefined}
>
```

- [ ] **Step 3: Build + test**

Run: `cd desktop && npm run build && npm test -- --run`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): keyboard-aware composer padding on mobile"
```

---

## Task 8: ChatStandalone — mount InstallPromptBanner

**Files:**
- Modify: `desktop/src/ChatStandalone.tsx`

- [ ] **Step 1: Update**

```tsx
// desktop/src/ChatStandalone.tsx
import { Suspense, lazy } from "react";
import { InstallPromptBanner } from "./shell/InstallPromptBanner";

const MessagesApp = lazy(() => import("./apps/MessagesApp").then((m) => ({ default: m.MessagesApp })));

export function ChatStandalone() {
  return (
    <div
      className="h-screen w-screen flex flex-col overflow-hidden"
      style={{ backgroundColor: "#1a1b2e", paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <InstallPromptBanner />
      <Suspense fallback={
        <div className="flex items-center justify-center h-full" style={{ color: "rgba(255,255,255,0.4)" }}>
          Loading…
        </div>
      }>
        <MessagesApp windowId="standalone-chat" title="taOS talk" />
      </Suspense>
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run: `cd desktop && npm run build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/ChatStandalone.tsx
git commit -m "feat(desktop): mount InstallPromptBanner in ChatStandalone"
```

---

## Task 9: chat.html — iOS splash meta tags

**Files:**
- Modify: `desktop/chat.html`

- [ ] **Step 1: Add splash meta tags**

In `desktop/chat.html`, in the `<head>` section right after the existing `apple-touch-icon` line, add:

```html
<!-- iOS splash screens (reuse 512 icon on #1a1b2e background; iOS scales it) -->
<link rel="apple-touch-startup-image" href="/static/icon-512.png" />
```

(Device-specific splash images are a future polish; the generic startup-image works on all iPhones and shows the icon centered.)

- [ ] **Step 2: Rebuild bundle (chat.html changes are picked up at build time)**

Run: `cd desktop && npm run build`
Expected: clean. Verify `/static/desktop/chat.html` contains the new meta tag.

- [ ] **Step 3: Commit**

```bash
git add desktop/chat.html
git commit -m "feat(pwa): iOS splash startup image for taOS talk PWA"
```

---

## Task 10: Rebuild desktop bundle (final)

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build
```

- [ ] **Step 2: Commit rebuilt assets**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for Messages PWA Phase 1"
```

---

## Task 11: Playwright mobile E2E

**Files:**
- Create: `tests/e2e/test_messages_pwa.py`

- [ ] **Step 1: Write env-gated mobile-viewport tests**

```python
"""Messages PWA mobile-viewport E2E.

Requires TAOS_E2E_URL set; skipped locally.
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


MOBILE_VIEWPORT = {"width": 375, "height": 667}


@pytest.fixture
def mobile_page(page: Page):
    page.set_viewport_size(MOBILE_VIEWPORT)
    return page


def test_mobile_thread_takeover(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    mobile_page.get_by_text("roundtable").first.click()
    # Pick a message and open thread
    first = mobile_page.locator("[data-message-id]").first
    first.tap()  # mobile tap
    mobile_page.get_by_role("button", name=re.compile("Reply in thread", re.I)).click()
    # Expect full-screen takeover — thread panel covers most of the viewport
    panel = mobile_page.get_by_role("complementary", name=re.compile("Thread", re.I))
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box is not None
    assert box["width"] >= 300  # takes the full width on 375px viewport
    # Back button returns
    mobile_page.get_by_role("button", name=re.compile("Back", re.I)).click()
    expect(panel).not_to_be_visible()


def test_mobile_overflow_bottom_sheet(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    mobile_page.get_by_text("roundtable").first.click()
    first = mobile_page.locator("[data-message-id]").first
    first.tap()
    mobile_page.get_by_role("button", name="More").click()
    # Bottom sheet shows the menu
    sheet_backdrop = mobile_page.get_by_test_id("bottom-sheet-backdrop")
    expect(sheet_backdrop).to_be_visible()
    # Menu items visible
    expect(mobile_page.get_by_role("menuitem", name=re.compile("Copy link", re.I))).to_be_visible()


def test_install_banner_hidden_when_no_event(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    # beforeinstallprompt is not emulated here; banner should not be visible.
    expect(mobile_page.get_by_role("region", name=re.compile("Install", re.I))).not_to_be_visible()
```

- [ ] **Step 2: Confirm it skips locally**

Run: `PYTHONPATH=. pytest tests/e2e/test_messages_pwa.py -v`
Expected: 3 SKIPPED (no `TAOS_E2E_URL`).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_messages_pwa.py
git commit -m "test(e2e): Messages PWA mobile viewport — thread takeover, bottom sheet, install banner"
```

---

## Final verification

- [ ] **Full test suite**

```bash
PYTHONPATH=. pytest tests/ -x -q --ignore=tests/e2e
cd desktop && npm test -- --run
cd desktop && npm run build
```

Expected: all pass / clean (3 pre-existing snap-zones failures acceptable).

- [ ] **Open PR**

```bash
git push -u origin feat/messages-pwa
gh pr create --base master \
  --title "Messages PWA Phase 1 — mobile UX + install prompt" \
  --body-file docs/superpowers/specs/2026-04-20-messages-pwa-phase-1-design.md
```
