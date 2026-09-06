# Shell Cross-App Drag-Drop Phase 1 — design

**Status:** Approved 2026-04-20.

## Goal

Provide a taOS-wide drag-drop primitive so apps can share content by drag: drop a file from the Files app into the chat composer, drag a message into the text editor, drop a knowledge item into a canvas. In-memory typed payloads inside the app; HTML5 dataTransfer mirror for drags into external browsers/docs.

## Non-goals

- Cross-window drag across *OS* windows (multi-monitor desktop shell). Same-tree only.
- Touch-device drag via long-press. Deferred to Messages PWA Phase 2 (mobile DnD has its own constraints).
- Multi-item drag selection.
- Intra-app reorder drag (already handled per-app where it exists).

## Decisions

1. **Scope:** Files + Messages + Knowledge items + Canvas blocks (all 4 kinds).
2. **Transport:** Hybrid — in-memory bus for rich typed payloads across apps + HTML5 dataTransfer mirror (so "drag to external browser" exports a URL / text).
3. **Rollout:** Incremental per-app integration. Shell primitives land first; each app's source/target wiring is its own Task.

## Payload types

Discriminated union exported from `desktop/src/shell/dnd/types.ts`:

```typescript
export type DragPayload =
  | { kind: "file"; path: string; mime_type: string; size: number; name: string }
  | { kind: "message"; channel_id: string; message_id: string; author_id: string; excerpt: string }
  | { kind: "knowledge"; id: string; title: string; url?: string }
  | { kind: "canvas-block"; canvas_id: string; block_id: string; block_type: string };

export type DragKind = DragPayload["kind"];
```

## Architecture

### `desktop/src/shell/dnd/dnd-bus.ts`

Singleton state + EventTarget-based pub/sub.

```typescript
interface BusState { payload: DragPayload | null }

const state: BusState = { payload: null };
const emitter = new EventTarget();
let staleTimer: ReturnType<typeof setTimeout> | null = null;

export function startDrag(payload: DragPayload): void;
export function endDrag(): void;
export function getCurrent(): DragPayload | null;
export function subscribe(listener: () => void): () => void;
```

- `startDrag` sets state, clears any prior stale timer, sets a new 30s timeout that auto-calls `endDrag` (safety against lost `dragend`).
- `endDrag` clears state + timer, dispatches "change".
- `subscribe(fn)` adds listener on the emitter; returns an unsubscribe.

### `desktop/src/shell/dnd/use-drag-source.ts`

React hook.

```typescript
export interface UseDragSourceOpts<T extends DragPayload> {
  payload: T;
  disabled?: boolean;
  /**
   * Optional HTML5 mirror so external drops get a usable text/uri.
   * e.g. { "text/uri-list": "https://host/chat/c1?msg=m9", "text/plain": "@tom: ..." }
   */
  htmlMirror?: Record<string, string>;
}

export function useDragSource<T extends DragPayload>(opts: UseDragSourceOpts<T>): {
  dragHandlers: {
    draggable: boolean;
    onDragStart: (e: React.DragEvent) => void;
    onDragEnd: () => void;
  };
};
```

- Applies `draggable={!disabled}`.
- `onDragStart`: call `startDrag(payload)`, also `e.dataTransfer.effectAllowed = "copy"`, for each mime in `htmlMirror` call `e.dataTransfer.setData(mime, value)`.
- `onDragEnd`: call `endDrag()`.

### `desktop/src/shell/dnd/use-drop-target.ts`

```typescript
export interface UseDropTargetOpts {
  accept: DragKind[];
  onDrop: (payload: DragPayload) => void | Promise<void>;
  disabled?: boolean;
}

export function useDropTarget(opts: UseDropTargetOpts): {
  dropHandlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
  isOver: boolean;
  isValidTarget: boolean;
};
```

- Subscribes to the bus. `isValidTarget = accept.includes(current?.kind)`.
- `isOver` tracked locally via enter/leave with a counter (dragenter on parent + child fires twice).
- `onDrop`: call `e.preventDefault()`, read `getCurrent()`, if `isValidTarget` call `opts.onDrop(payload)` and catch-log errors.

### Integration points

Per-app wiring (separate tasks after the primitive lands):

| App | Source wiring | Target wiring |
|---|---|---|
| FilesApp | File rows → `useDragSource({kind:"file",...})` with `htmlMirror: {"text/plain": path}` | — |
| MessagesApp | Message row hover → `useDragSource({kind:"message",...})` with mirror `"text/uri-list": deepLink`; composer + attachments bar → `useDropTarget({accept:["file","knowledge"]})` | — |
| LibraryApp | Knowledge row → `useDragSource({kind:"knowledge",...})` with `"text/uri-list": url` | — |
| CanvasApp | Blocks → `useDragSource({kind:"canvas-block",...})`; canvas surface → `useDropTarget({accept:["file","message","knowledge"]})` | — |
| TextEditorApp | — | `useDropTarget({accept:["message","knowledge","file"]})` — inserts markdown rep at caret |

### Visual feedback

- Drop target elements get subtle ring when `isValidTarget`: `ring-2 ring-sky-400/40`.
- Stronger ring + bg tint when `isOver`: `ring-2 ring-sky-400 bg-sky-500/5`.
- Non-target or invalid-type drops don't react.
- Shell root can optionally set `body.data-dragging="true"` while any drag is in flight for cursor styling.

## Error handling

- Drop callback throws → caught in the hook, `console.warn`, no crash. Toast is caller's responsibility.
- `dragend` never fires (rare — pointer capture lost) → bus auto-clears after 30s. Stale payload never reaches targets after that timeout.
- `dataTransfer.setData` throws on some browsers (old Safari) → wrapped in try/catch; HTML5 mirror is best-effort.
- Drop target mounted during drag: subscribes on mount, reads current bus state → renders `isValidTarget` immediately.
- Drop target unmounted during drag: cleanup removes listener; no stale references.

## Testing

### Vitest unit

- `dnd-bus.test.ts`: `startDrag` → `getCurrent` returns payload; `endDrag` → null; `subscribe` fires on both; stale timeout clears state after 30s; starting a new drag resets the timer.
- `use-drag-source.test.tsx`: `dragHandlers.onDragStart` calls `startDrag`; `htmlMirror` entries applied to `dataTransfer.setData`; `disabled=true` disables draggable.
- `use-drop-target.test.tsx`: `isValidTarget` reflects bus state matching `accept`; `onDrop` invokes callback only when valid; `isOver` counter correctly handles nested children.

### E2E (Playwright, gated on `TAOS_E2E_URL`)

- Drag a file row from Files → drop onto Messages composer → attachment chip appears in AttachmentsBar.

## Rollout

**Tasks grouped in this PR:**
1. Shell primitives (bus + 2 hooks + types). Land first.
2. FilesApp source + MessagesApp target (closes the chat-attachments UX gap — most valuable pair).
3. MessagesApp source + TextEditorApp target (quote-into-notes).
4. LibraryApp source (knowledge → chat / canvas).
5. CanvasApp source + target.
6. Playwright E2E.

Tasks 2-5 are independent after Task 1 and could ship as separate follow-up PRs. Controller will decide at execution time whether to bundle all or stop at Task 2 to get the primitive adopted before wiring the remaining apps.

## Out of scope / future

- Touch-device drag (mobile PWA DnD).
- Multi-item selection drag.
- Cross-window / multi-monitor drag.
- Server-side drag (e.g. drag a message between users' taOS instances).
