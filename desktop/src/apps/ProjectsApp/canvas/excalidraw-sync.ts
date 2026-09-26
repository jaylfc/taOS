import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { CanvasElement, CanvasElementInput, CanvasElementKind } from "./canvas-api";

// Excalidraw scene -> CanvasElement sync core.
//
// A pure, React-free layer between Excalidraw's `onChange` and the project
// canvas REST API. The interactive board feeds it every scene it applies from
// the server (`markRemote`) and every scene Excalidraw reports (`onLocalChange`);
// it turns the difference into POST / PATCH / DELETE calls and tells the board
// which SSE events are echoes of its own writes (`shouldApplyRemote*`).
//
// Invariants:
// - Diff by (id, version, versionNonce). An element whose version equals the
//   last remote-applied one, or the last locally-processed one, is an echo and
//   produces no write. This is what stops the SSE -> updateScene -> onChange ->
//   PATCH -> SSE loop.
// - Writes are debounced per row (trailing DEBOUNCE_MS) and merged, so a drag
//   sends one PATCH, not one per frame. `flush()` sends now, `dispose()` drops.
// - A pending local write is based on the remote stamp (row updated_at) seen
//   when the edit started. If a NEWER foreign stamp arrives before the write is
//   sent, the write is dropped and `onConflict` is told: the server version
//   wins, a stale local edit never clobbers a newer remote change. Stamps
//   produced by our own writes are never conflicts.
// - A user_shape PATCH never drops `payload.tldraw_shape`: when the known row
//   carries the legacy blob it is copied into the payload beside
//   `excalidraw_element`. (The server-side guard, when present, is a second
//   line; this layer does not rely on it.)
// - No runtime import of @excalidraw/excalidraw (types only), so this module
//   and its tests run without a jsdom canvas.

export const DEBOUNCE_MS = 150;
// The server rejects payloads over 64 KiB (_CANVAS_PAYLOAD_MAX_BYTES). Stay
// under a soft cap so the JSON envelope never crosses the hard one.
export const PAYLOAD_HARD_CAP_BYTES = 64 * 1024;
export const PAYLOAD_SOFT_CAP_BYTES = 60 * 1024;

export interface SyncCustomData {
  taos_id?: string;
  taos_kind?: string;
  taos_placeholder?: boolean;
  // Row updated_at the board stamped when it applied the element.
  taos_updated_at?: number;
  [key: string]: unknown;
}

// The subset of an Excalidraw element this layer reads. A real
// `ExcalidrawElement` is structurally assignable to it (the readonly arrays
// and the index signature keep the extra properties).
export interface SyncSceneElement {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  angle: number;
  version: number;
  versionNonce: number;
  isDeleted: boolean;
  groupIds: readonly string[];
  locked?: boolean;
  containerId?: string | null;
  boundElements?: readonly { id: string; type: string }[] | null;
  text?: string;
  points?: readonly (readonly [number, number])[];
  pressures?: readonly number[];
  customData?: SyncCustomData;
  [key: string]: unknown;
}

// Compile-time proof the real element type feeds `onLocalChange` unchanged.
type _AssertAssignable = ExcalidrawElement extends SyncSceneElement ? true : never;
const _assertAssignable: _AssertAssignable = true;
void _assertAssignable;

export type SceneSyncApi = {
  addElement(projectId: string, input: CanvasElementInput): Promise<CanvasElement>;
  updateElement(
    projectId: string, elementId: string, patch: Partial<CanvasElementInput>,
  ): Promise<CanvasElement>;
  deleteElement(projectId: string, elementId: string): Promise<boolean>;
};

export interface SceneSyncOptions {
  projectId: string;
  // Nested-element scope for new rows (ProjectWorkspace passes elementId).
  elementId?: string | null;
  api: SceneSyncApi;
  // Current row for an id (the board's canvas store). Used for the kind, the
  // payload to carry on a text edit, and the legacy tldraw_shape blob.
  getElement?: (id: string) => CanvasElement | undefined;
  // Called with the row id whenever a pending local write is dropped because a
  // newer remote change landed first.
  onConflict?: (rowId: string) => void;
  onError?: (err: unknown, rowId: string) => void;
  debounceMs?: number;
}

export interface SceneSync {
  // Record the scene the board just applied from the server.
  markRemote(elements: readonly SyncSceneElement[]): void;
  // Excalidraw onChange. Resolves once the scene has been diffed and any
  // immediate writes (POST, DELETE) are issued; PATCHes stay debounced.
  onLocalChange(elements: readonly SyncSceneElement[]): Promise<void>;
  // True when an SSE row is a foreign change the board should apply; false
  // when it is the echo of a write this sync issued.
  shouldApplyRemote(row: CanvasElement): boolean;
  shouldApplyRemoteDelete(rowId: string): boolean;
  // Send every pending PATCH now.
  flush(): Promise<void>;
  // Cancel timers and drop pending writes.
  dispose(): void;
}

// --- geometry -----------------------------------------------------------

// Excalidraw keeps x/y as the unrotated top-left and rotates `angle` about the
// centre. taOS rows rotate about the top-left, so the row x/y is where the
// top-left corner ends up after that rotation.
export function angleToRotation(
  el: { x: number; y: number; width: number; height: number; angle: number },
): { x: number; y: number; rotation: number } {
  const a = el.angle || 0;
  if (a === 0) return { x: el.x, y: el.y, rotation: 0 };
  const hw = el.width / 2;
  const hh = el.height / 2;
  const cx = el.x + hw;
  const cy = el.y + hh;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  return {
    x: cx - hw * cos + hh * sin,
    y: cy - hw * sin - hh * cos,
    rotation: a,
  };
}

function geometryPatch(el: SyncSceneElement): Pick<CanvasElementInput, "x" | "y" | "w" | "h" | "rotation"> {
  const r = angleToRotation(el);
  return { x: r.x, y: r.y, w: el.width, h: el.height, rotation: r.rotation };
}

// --- freedraw simplification ---------------------------------------------

function perpendicularDistance(
  p: readonly [number, number], a: readonly [number, number], b: readonly [number, number],
): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

// Ramer-Douglas-Peucker, iterative. Keeps the endpoints and every point whose
// distance from the local chord exceeds `epsilon`; pressures follow the kept
// indices.
export function simplifyFreedraw(
  points: readonly (readonly [number, number])[],
  pressures: readonly number[] | undefined,
  epsilon: number,
): { points: [number, number][]; pressures: number[] } {
  const n = points.length;
  const keep = new Array<boolean>(n).fill(false);
  if (n > 0) keep[0] = true;
  if (n > 1) keep[n - 1] = true;
  const stack: [number, number][] = n > 2 ? [[0, n - 1]] : [];
  while (stack.length) {
    const [s, e] = stack.pop()!;
    let maxD = -1;
    let idx = -1;
    for (let i = s + 1; i < e; i++) {
      const d = perpendicularDistance(points[i]!, points[s]!, points[e]!);
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (idx !== -1 && maxD > epsilon) {
      keep[idx] = true;
      if (idx - s > 1) stack.push([s, idx]);
      if (e - idx > 1) stack.push([idx, e]);
    }
  }
  const outP: [number, number][] = [];
  const outPr: number[] = [];
  for (let i = 0; i < n; i++) {
    if (!keep[i]) continue;
    const p = points[i]!;
    outP.push([p[0], p[1]]);
    if (pressures && i < pressures.length) outPr.push(pressures[i]!);
  }
  return { points: outP, pressures: pressures ? outPr : [] };
}

function byteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

// Shrink a freedraw element until its payload envelope fits the soft cap.
function fitPayload(payload: Record<string, unknown>, el: SyncSceneElement): Record<string, unknown> {
  if (byteLength(payload) <= PAYLOAD_SOFT_CAP_BYTES) return payload;
  if (el.type !== "freedraw" || !el.points || el.points.length < 3) return payload;
  let epsilon = 0.25;
  let current: SyncSceneElement = el;
  for (let i = 0; i < 24; i++) {
    const s = simplifyFreedraw(el.points, el.pressures, epsilon);
    current = {
      ...el,
      points: s.points,
      ...(el.pressures ? { pressures: s.pressures } : {}),
    };
    const candidate = { ...payload, excalidraw_element: current };
    if (byteLength(candidate) <= PAYLOAD_SOFT_CAP_BYTES) return candidate;
    epsilon *= 2;
  }
  return { ...payload, excalidraw_element: current };
}

// --- helpers --------------------------------------------------------------

const DIAGRAM_KINDS = new Set<string>(["mermaid", "flowchart"]);
const TEXT_PAYLOAD_KINDS = new Set<string>(["note", "text"]);

function versionKey(el: SyncSceneElement): string {
  return `${el.version}:${el.versionNonce}`;
}

// Strip the legacy blob defensively before an element is stored: an
// excalidraw_element never nests a tldraw_shape.
function stripBlob(el: SyncSceneElement): SyncSceneElement {
  if (el.customData && "tldraw_shape" in el.customData) {
    const { tldraw_shape: _drop, ...rest } = el.customData;
    void _drop;
    return { ...el, customData: rest };
  }
  return el;
}

interface PendingWrite {
  rowId: string;
  basedOn: number | undefined;
  geometry?: Pick<CanvasElementInput, "x" | "y" | "w" | "h" | "rotation">;
  // Group move: the row's new x/y derived from the parts' delta.
  groupXY?: { x: number; y: number };
  groupDelta?: { dx: number; dy: number };
  text?: string;
  shape?: SyncSceneElement;
  timer?: ReturnType<typeof setTimeout>;
}

export function createSceneSync(opts: SceneSyncOptions): SceneSync {
  const { projectId, api } = opts;
  const elementId = opts.elementId ?? null;
  const debounceMs = opts.debounceMs ?? DEBOUNCE_MS;

  // Last version applied from the server, per scene element id.
  const remoteVersions = new Map<string, string>();
  // Last version this sync processed locally (a repeated onChange is a no-op).
  const localVersions = new Map<string, string>();
  // Remote geometry per scene element id, for group deltas.
  const remoteXY = new Map<string, { x: number; y: number }>();
  // Remote text per scene element id, so unchanged labels never write payload.
  const remoteText = new Map<string, string>();
  // Row stamp (updated_at) last applied from the server, per row id.
  const remoteStamp = new Map<string, number | undefined>();
  // Stamps produced by our own writes: an SSE row carrying one is an echo.
  const ownStamps = new Map<string, Set<number>>();
  const ownDeletes = new Set<string>();
  // Rows as returned by our own writes (fresher than the store until the echo).
  const known = new Map<string, CanvasElement>();
  // Scene element id -> row id, for elements this sync created.
  const createdRows = new Map<string, string>();
  const postsInFlight = new Map<string, Promise<void>>();
  const deleted = new Set<string>();
  // Diagram row id -> scene ids of its parts (for rebasing after a group move).
  const partsByRow = new Map<string, Set<string>>();
  const pending = new Map<string, PendingWrite>();
  let disposed = false;

  function rememberOwn(row: CanvasElement): void {
    known.set(row.id, row);
    remoteStamp.set(row.id, row.updated_at);
    let s = ownStamps.get(row.id);
    if (!s) { s = new Set(); ownStamps.set(row.id, s); }
    s.add(row.updated_at);
  }

  function rowFor(rowId: string): CanvasElement | undefined {
    return known.get(rowId) ?? opts.getElement?.(rowId);
  }

  function kindFor(rowId: string, el: SyncSceneElement): CanvasElementKind | string | undefined {
    return rowFor(rowId)?.kind ?? el.customData?.taos_kind;
  }

  // Resolve a scene element to its row id. A bound label maps to its
  // container's row; an element this sync created maps to the row it made.
  function rowIdFor(el: SyncSceneElement, byId: Map<string, SyncSceneElement>): string | undefined {
    const direct = el.customData?.taos_id;
    if (typeof direct === "string" && direct) return direct;
    if (el.containerId) {
      const c = byId.get(el.containerId);
      const viaContainer = c?.customData?.taos_id;
      if (typeof viaContainer === "string" && viaContainer) return viaContainer;
      return createdRows.get(el.containerId);
    }
    return createdRows.get(el.id);
  }

  function isLabel(el: SyncSceneElement): boolean {
    return el.type === "text" && !!el.containerId;
  }

  function isConflict(p: PendingWrite): boolean {
    const now = remoteStamp.get(p.rowId);
    if (now === p.basedOn) return false;
    if (now !== undefined && ownStamps.get(p.rowId)?.has(now)) return false;
    return true;
  }

  function schedule(rowId: string, fill: (p: PendingWrite) => void): void {
    let p = pending.get(rowId);
    if (!p) {
      p = { rowId, basedOn: remoteStamp.get(rowId) };
      pending.set(rowId, p);
    }
    fill(p);
    if (p.timer) clearTimeout(p.timer);
    p.timer = setTimeout(() => { void send(rowId); }, debounceMs);
  }

  function buildPatch(p: PendingWrite): Partial<CanvasElementInput> | null {
    const patch: Partial<CanvasElementInput> = {};
    if (p.groupXY) {
      patch.x = p.groupXY.x;
      patch.y = p.groupXY.y;
      return patch;
    }
    if (p.geometry) Object.assign(patch, p.geometry);
    const row = rowFor(p.rowId);
    if (p.shape) {
      let payload: Record<string, unknown> = { excalidraw_element: stripBlob(p.shape) };
      const blob = row?.payload?.tldraw_shape;
      if (blob !== undefined) payload = { tldraw_shape: blob, ...payload };
      patch.payload = fitPayload(payload, p.shape);
    } else if (p.text !== undefined) {
      patch.payload = { ...(row?.payload ?? {}), text: p.text };
    }
    return Object.keys(patch).length ? patch : null;
  }

  async function send(rowId: string): Promise<void> {
    const p = pending.get(rowId);
    if (!p || disposed) return;
    pending.delete(rowId);
    if (p.timer) clearTimeout(p.timer);
    if (deleted.has(rowId)) return;
    if (isConflict(p)) {
      opts.onConflict?.(rowId);
      return;
    }
    const patch = buildPatch(p);
    if (!patch) return;
    try {
      const row = await api.updateElement(projectId, rowId, patch);
      if (row) {
        rememberOwn(row);
        if (p.groupDelta) {
          // Rebase the parts' remote origin on the geometry just written so
          // the next drag delta is measured from the row we now hold.
          const { dx, dy } = p.groupDelta;
          for (const partId of partsByRow.get(rowId) ?? []) {
            const o = remoteXY.get(partId);
            if (o) remoteXY.set(partId, { x: o.x + dx, y: o.y + dy });
          }
        }
      }
    } catch (err) {
      opts.onError?.(err, rowId);
    }
  }

  function markRemote(elements: readonly SyncSceneElement[]): void {
    for (const el of elements) {
      remoteVersions.set(el.id, versionKey(el));
      localVersions.delete(el.id);
      remoteXY.set(el.id, { x: el.x, y: el.y });
      if (typeof el.text === "string") remoteText.set(el.id, el.text);
      const rowId = el.customData?.taos_id;
      const stamp = el.customData?.taos_updated_at;
      if (typeof rowId === "string" && rowId && typeof stamp === "number") {
        const prev = remoteStamp.get(rowId);
        if (prev !== stamp) {
          remoteStamp.set(rowId, stamp);
          // A foreign stamp supersedes anything we cached from our own writes.
          if (!ownStamps.get(rowId)?.has(stamp)) known.delete(rowId);
        }
      }
    }
  }

  async function createShape(el: SyncSceneElement): Promise<void> {
    const shape = stripBlob(el);
    const geom = geometryPatch(el);
    const payload = fitPayload({ excalidraw_element: shape }, el);
    const input: CanvasElementInput = {
      id: el.id,
      kind: "user_shape",
      ...geom,
      payload,
      element_id: elementId,
    };
    try {
      const row = await api.addElement(projectId, input);
      if (row) {
        createdRows.set(el.id, row.id);
        rememberOwn(row);
      }
    } catch (err) {
      opts.onError?.(err, el.id);
    }
  }

  async function onLocalChange(elements: readonly SyncSceneElement[]): Promise<void> {
    if (disposed) return;
    const byId = new Map<string, SyncSceneElement>();
    for (const el of elements) byId.set(el.id, el);
    const immediate: Promise<void>[] = [];
    const deletesThisPass = new Set<string>();

    for (const el of elements) {
      const key = versionKey(el);
      if (remoteVersions.get(el.id) === key) continue; // server echo
      if (localVersions.get(el.id) === key) continue; // repeated onChange
      localVersions.set(el.id, key);

      const rowId = rowIdFor(el, byId);

      if (el.isDeleted) {
        if (!rowId) continue; // never persisted: nothing to delete
        if (deleted.has(rowId) || deletesThisPass.has(rowId)) continue;
        deletesThisPass.add(rowId);
        deleted.add(rowId);
        const p = pending.get(rowId);
        if (p?.timer) clearTimeout(p.timer);
        pending.delete(rowId);
        ownDeletes.add(rowId);
        const run = async () => {
          await postsInFlight.get(el.id);
          try {
            await api.deleteElement(projectId, rowId);
          } catch (err) {
            opts.onError?.(err, rowId);
          }
        };
        immediate.push(run());
        continue;
      }

      if (!rowId) {
        // New user-drawn element. Labels of new shapes ride along inside the
        // container's excalidraw_element on its next edit.
        if (isLabel(el) || postsInFlight.has(el.id)) continue;
        const post = createShape(el).finally(() => postsInFlight.delete(el.id));
        postsInFlight.set(el.id, post);
        immediate.push(post);
        continue;
      }

      if (deleted.has(rowId)) continue;
      const kind = kindFor(rowId, el);
      const placeholder = el.customData?.taos_placeholder === true;

      if (kind === "mindmap_edge") continue; // derived from its endpoints

      if (kind !== undefined && DIAGRAM_KINDS.has(kind)) {
        let parts = partsByRow.get(rowId);
        if (!parts) { parts = new Set(); partsByRow.set(rowId, parts); }
        parts.add(el.id);
        const origin = remoteXY.get(el.id);
        const row = rowFor(rowId);
        if (!origin || !row) continue;
        const dx = el.x - origin.x;
        const dy = el.y - origin.y;
        if (dx === 0 && dy === 0) continue;
        schedule(rowId, (p) => {
          p.groupXY = { x: row.x + dx, y: row.y + dy };
          p.groupDelta = { dx, dy };
        });
        continue;
      }

      if (isLabel(el)) {
        if (placeholder) continue;
        if (kind !== undefined && !TEXT_PAYLOAD_KINDS.has(kind)) continue; // derived label (link)
        const text = el.text ?? "";
        const prev = remoteText.has(el.id) ? remoteText.get(el.id) : rowFor(rowId)?.payload?.text;
        if (text === prev) continue;
        remoteText.set(el.id, text);
        schedule(rowId, (p) => { p.text = text; });
        continue;
      }

      // Container / standalone element carrying the row geometry.
      const geometry = geometryPatch(el);
      const isText = kind === "text" && el.type === "text";
      const textChanged = isText && el.text !== undefined && el.text !== (remoteText.get(el.id) ?? rowFor(rowId)?.payload?.text);
      if (textChanged) remoteText.set(el.id, el.text as string);
      // Unknown kinds (no row, no taos_kind) get geometry only: never a
      // payload this layer cannot vouch for.
      const asShape = !placeholder && kind === "user_shape";
      schedule(rowId, (p) => {
        p.geometry = geometry;
        if (textChanged) p.text = el.text as string;
        if (asShape) p.shape = el;
      });
    }

    if (immediate.length) await Promise.all(immediate);
  }

  function shouldApplyRemote(row: CanvasElement): boolean {
    if (ownStamps.get(row.id)?.has(row.updated_at)) return false;
    return true;
  }

  function shouldApplyRemoteDelete(rowId: string): boolean {
    return !ownDeletes.has(rowId);
  }

  async function flush(): Promise<void> {
    const ids = Array.from(pending.keys());
    await Promise.all(ids.map((id) => send(id)));
    await Promise.all(Array.from(postsInFlight.values()));
  }

  function dispose(): void {
    disposed = true;
    for (const p of pending.values()) if (p.timer) clearTimeout(p.timer);
    pending.clear();
  }

  return { markRemote, onLocalChange, shouldApplyRemote, shouldApplyRemoteDelete, flush, dispose };
}
