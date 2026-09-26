import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  createSceneSync,
  angleToRotation,
  simplifyFreedraw,
  PAYLOAD_SOFT_CAP_BYTES,
  type SyncSceneElement,
  type SceneSyncApi,
} from "../canvas/excalidraw-sync";
import type { CanvasElement, CanvasElementInput } from "../canvas/canvas-api";

// ---------------------------------------------------------------------------
// Fixtures: a fake canvasApi that records every write, and row/scene builders.
// ---------------------------------------------------------------------------

type Call =
  | { op: "POST"; input: CanvasElementInput }
  | { op: "PATCH"; id: string; patch: Partial<CanvasElementInput> }
  | { op: "DELETE"; id: string };

function row(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "n1",
    project_id: "prj",
    kind: "note",
    author_kind: "user",
    author_id: "u",
    x: 10,
    y: 20,
    w: 100,
    h: 50,
    rotation: 0,
    z_index: 0,
    payload: { text: "hello", color: "blue", font_size: 14 },
    element_id: null,
    created_at: 1,
    updated_at: 1,
    deleted_at: null,
    ...over,
  };
}

function makeApi(rows: Record<string, CanvasElement> = {}) {
  const calls: Call[] = [];
  let clock = 100;
  const api: SceneSyncApi = {
    async addElement(_projectId, input) {
      calls.push({ op: "POST", input });
      const created = row({
        id: input.id ?? `srv-${calls.length}`,
        kind: input.kind,
        x: input.x, y: input.y, w: input.w, h: input.h,
        rotation: input.rotation ?? 0,
        payload: input.payload,
        element_id: input.element_id ?? null,
        updated_at: ++clock,
      });
      rows[created.id] = created;
      return created;
    },
    async updateElement(_projectId, id, patch) {
      calls.push({ op: "PATCH", id, patch });
      const existing = rows[id] ?? row({ id });
      const updated = { ...existing, ...patch, updated_at: ++clock } as CanvasElement;
      rows[id] = updated;
      return updated;
    },
    async deleteElement(_projectId, id) {
      calls.push({ op: "DELETE", id });
      delete rows[id];
      return true;
    },
  };
  return { api, calls, rows, getElement: (id: string) => rows[id] };
}

let nonce = 1000;
function el(over: Partial<SyncSceneElement> & { id: string }): SyncSceneElement {
  return {
    type: "rectangle",
    x: 10,
    y: 20,
    width: 100,
    height: 50,
    angle: 0,
    version: 1,
    versionNonce: nonce++,
    isDeleted: false,
    groupIds: [],
    ...over,
  };
}

// A note row as the board renders it: a rectangle container plus a bound
// label text element, both stamped with the row id.
function noteScene(id = "n1", text = "hello", version = 1): SyncSceneElement[] {
  return [
    el({
      id, type: "rectangle", version,
      customData: { taos_id: id, taos_kind: "note" },
      boundElements: [{ id: `${id}-label`, type: "text" }],
    }),
    el({
      id: `${id}-label`, type: "text", text, version,
      containerId: id,
      customData: { taos_id: id, taos_kind: "note" },
    }),
  ];
}

function bump(e: SyncSceneElement, over: Partial<SyncSceneElement> = {}): SyncSceneElement {
  return { ...e, ...over, version: e.version + 1, versionNonce: nonce++ };
}

async function settle() {
  // Drain microtasks queued by the async write path.
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

const PATCHES = (calls: Call[]) => calls.filter((c) => c.op === "PATCH") as Extract<Call, { op: "PATCH" }>[];
const POSTS = (calls: Call[]) => calls.filter((c) => c.op === "POST") as Extract<Call, { op: "POST" }>[];
const DELETES = (calls: Call[]) => calls.filter((c) => c.op === "DELETE") as Extract<Call, { op: "DELETE" }>[];

// ---------------------------------------------------------------------------

describe("createSceneSync", () => {
  it("a scene applied via markRemote then echoed by onChange produces ZERO writes", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const scene = noteScene();
    sync.markRemote(scene);
    // Excalidraw fires onChange right after updateScene with the same elements.
    await sync.onLocalChange(scene);
    await sync.onLocalChange(scene);
    await vi.advanceTimersByTimeAsync(1000);
    await sync.flush();
    expect(calls).toEqual([]);
  });

  it("moving a note emits exactly one PATCH with x/y (throttled)", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    // A drag: many onChange frames, each with a bumped version.
    let cur = rect;
    for (let i = 1; i <= 20; i++) {
      cur = bump(cur, { x: 10 + i * 5, y: 20 + i * 2 });
      await sync.onLocalChange([cur, label]);
      await vi.advanceTimersByTimeAsync(10);
    }
    expect(calls).toEqual([]); // nothing before the trailing edge
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    expect(patches[0]).toMatchObject({ id: "n1", patch: { x: 110, y: 60, w: 100, h: 50, rotation: 0 } });
    expect(patches[0]!.patch.payload).toBeUndefined();
  });

  it("converts the centre angle back to a top-left rotation on a geometry PATCH", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    const r = Math.PI / 2;
    // Excalidraw keeps x/y as the unrotated top-left and rotates about the centre.
    await sync.onLocalChange([bump(rect, { angle: r }), label]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const p = PATCHES(calls)[0]!.patch;
    const expected = angleToRotation({ x: 10, y: 20, width: 100, height: 50, angle: r });
    expect(p.rotation).toBeCloseTo(r);
    expect(p.x).toBeCloseTo(expected.x);
    expect(p.y).toBeCloseTo(expected.y);
    // Round trip with the plan's forward formula (top-left rotation -> centre).
    const cx = expected.x + (100 / 2) * Math.cos(r) - (50 / 2) * Math.sin(r);
    const cy = expected.y + (100 / 2) * Math.sin(r) + (50 / 2) * Math.cos(r);
    expect(cx - 50).toBeCloseTo(10);
    expect(cy - 25).toBeCloseTo(20);
  });

  it("editing a note's bound label emits PATCH payload.text on the NOTE row", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    await sync.onLocalChange([rect, bump(label, { text: "edited" })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.id).toBe("n1"); // the container's row, not "n1-label"
    // The whole payload is carried (PATCH replaces payload wholesale server-side).
    expect(patches[0]!.patch.payload).toEqual({ text: "edited", color: "blue", font_size: 14 });
  });

  it("a text change on a link label never writes payload (label is derived)", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({ kind: "link", payload: { url: "https://x.test", title: "X" } }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const scene = noteScene("n1", "X").map((e) => ({ ...e, customData: { taos_id: "n1", taos_kind: "link" } }));
    sync.markRemote(scene);
    await sync.onLocalChange([scene[0]!, bump(scene[1]!, { text: "renamed" })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls).every((p) => p.patch.payload === undefined)).toBe(true);
  });

  it("isDeleted element emits DELETE for its row", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    // Excalidraw soft-deletes the container and its bound text together.
    await sync.onLocalChange([bump(rect, { isDeleted: true }), bump(label, { isDeleted: true })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(DELETES(calls)).toEqual([{ op: "DELETE", id: "n1" }]);
    expect(PATCHES(calls)).toHaveLength(0);
  });

  it("a new rectangle with no taos_id emits POST kind=user_shape payload.excalidraw_element", async () => {
    const { api, calls } = makeApi();
    const sync = createSceneSync({ projectId: "prj", elementId: "elm-1", api });
    const fresh = el({ id: "abc123", type: "rectangle", x: 5, y: 6, width: 30, height: 40 });
    await sync.onLocalChange([fresh]);
    await settle();
    const posts = POSTS(calls);
    expect(posts).toHaveLength(1);
    expect(posts[0]!.input).toMatchObject({
      id: "abc123",
      kind: "user_shape",
      x: 5, y: 6, w: 30, h: 40, rotation: 0,
      element_id: "elm-1",
    });
    expect(posts[0]!.input.payload.excalidraw_element).toMatchObject({ id: "abc123", type: "rectangle" });
    expect(Object.keys(posts[0]!.input.payload)).toEqual(["excalidraw_element"]);
    // A second onChange for the same unchanged element must not POST again.
    await sync.onLocalChange([fresh]);
    await settle();
    expect(POSTS(calls)).toHaveLength(1);
  });

  it("an edit after the POST resolves emits a PATCH on the new row, not a second POST", async () => {
    const { api, calls } = makeApi();
    const sync = createSceneSync({ projectId: "prj", api });
    const fresh = el({ id: "abc123", type: "rectangle" });
    await sync.onLocalChange([fresh]);
    await settle();
    await sync.onLocalChange([bump(fresh, { x: 99 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(POSTS(calls)).toHaveLength(1);
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.id).toBe("abc123");
    expect(patches[0]!.patch.x).toBe(99);
    expect(patches[0]!.patch.payload).toEqual({ excalidraw_element: expect.objectContaining({ id: "abc123", x: 99 }) });
  });

  it("a user_shape edit on a legacy row carries tldraw_shape and adds excalidraw_element (never drops the blob)", async () => {
    const blob = { type: "geo", props: { w: 1 } };
    const legacy = row({ id: "u1", kind: "user_shape", payload: { tldraw_shape: blob } });
    const { api, calls, getElement } = makeApi({ u1: legacy });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const shape = el({ id: "u1", type: "ellipse", customData: { taos_id: "u1", taos_kind: "user_shape" } });
    sync.markRemote([shape]);
    await sync.onLocalChange([bump(shape, { x: 50 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    const payload = patches[0]!.patch.payload!;
    expect(Object.keys(payload).sort()).toEqual(["excalidraw_element", "tldraw_shape"]);
    expect(payload.tldraw_shape).toEqual(blob);
    expect(payload.excalidraw_element).toMatchObject({ id: "u1", type: "ellipse", x: 50 });
    expect(patches[0]!.patch).toMatchObject({ x: 50, y: 20, w: 100, h: 50 });
    // A second edit after the first PATCH resolved still carries the blob.
    await sync.onLocalChange([bump(bump(shape, { x: 50 }), { x: 60 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls)).toHaveLength(2);
    expect(PATCHES(calls)[1]!.patch.payload!.tldraw_shape).toEqual(blob);
  });

  it("a user_shape edit on a row without tldraw_shape sends excalidraw_element only", async () => {
    const fresh = row({ id: "u2", kind: "user_shape", payload: { excalidraw_element: { id: "u2", type: "ellipse" } } });
    const { api, calls, getElement } = makeApi({ u2: fresh });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const shape = el({ id: "u2", type: "ellipse", customData: { taos_id: "u2", taos_kind: "user_shape" } });
    sync.markRemote([shape]);
    await sync.onLocalChange([bump(shape, { x: 50 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const payload = PATCHES(calls)[0]!.patch.payload!;
    expect(Object.keys(payload)).toEqual(["excalidraw_element"]);
    expect(payload.excalidraw_element).toMatchObject({ id: "u2", x: 50 });
  });

  it("the SSE echo of our own PATCH is not re-applied, a foreign update is", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({ updated_at: 5 }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    await sync.onLocalChange([bump(rect, { x: 70 }), label]);
    await sync.flush();
    expect(PATCHES(calls)).toHaveLength(1);
    const echoed = getElement("n1")!;
    expect(sync.shouldApplyRemote(echoed)).toBe(false);
    // Asking twice is stable (SSE may redeliver).
    expect(sync.shouldApplyRemote(echoed)).toBe(false);
    const foreign = { ...echoed, x: 500, updated_at: echoed.updated_at + 1 };
    expect(sync.shouldApplyRemote(foreign)).toBe(true);
  });

  it("the SSE echo of our own DELETE is filtered, a foreign delete is not", async () => {
    const { api, getElement } = makeApi({ n1: row({}), n2: row({ id: "n2" }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    await sync.onLocalChange([bump(rect, { isDeleted: true }), bump(label, { isDeleted: true })]);
    await settle();
    expect(sync.shouldApplyRemoteDelete("n1")).toBe(false);
    expect(sync.shouldApplyRemoteDelete("n2")).toBe(true);
  });

  it("does not loop at the SSE level: applying every event through shouldApplyRemote converges (iteration cap)", async () => {
    // A naive board applies its own echo, rebuilds the scene (new version and
    // nonce), fires onChange, and the sync PATCHes again forever. Bounded here.
    const { api, calls, getElement } = makeApi({ n1: row({ updated_at: 5 }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    let scene = noteScene().map((e) => ({ ...e, customData: { ...e.customData, taos_updated_at: 5 } }));
    sync.markRemote(scene);
    await sync.onLocalChange([bump(scene[0]!, { x: 33 }), scene[1]!]);
    const CAP = 25;
    let rounds = 0;
    let applied = 0;
    while (rounds < CAP) {
      rounds++;
      await vi.advanceTimersByTimeAsync(200);
      await settle();
      const r = getElement("n1")!;
      if (!sync.shouldApplyRemote(r)) break; // own echo: nothing to apply
      applied++;
      scene = scene.map((e) => bump(e, { x: r.x, customData: { ...e.customData, taos_updated_at: r.updated_at } }));
      sync.markRemote(scene);
      await sync.onLocalChange(scene);
    }
    expect(rounds).toBeLessThan(CAP);
    expect(applied).toBe(0);
    expect(PATCHES(calls)).toHaveLength(1);
  });

  it("moving a mermaid group emits one PATCH on the mermaid row, none per sub-element", async () => {
    const { api, calls, getElement } = makeApi({ m1: row({ id: "m1", kind: "mermaid", x: 100, y: 200, payload: { source: "graph TD" } }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const parts = ["a", "b", "c", "e"].map((s, i) =>
      el({
        id: `m1-${s}`,
        type: i === 3 ? "arrow" : "rectangle",
        x: 100 + i * 50,
        y: 200,
        groupIds: ["m1-group"],
        locked: true,
        customData: { taos_id: "m1", taos_kind: "mermaid" },
      }),
    );
    sync.markRemote(parts);
    // The user drags the locked group by (+30, -10) over two frames.
    const frame1 = parts.map((p) => bump(p, { x: p.x + 10, y: p.y - 5 }));
    await sync.onLocalChange(frame1);
    await vi.advanceTimersByTimeAsync(10);
    const frame2 = frame1.map((p) => bump(p, { x: p.x + 20, y: p.y - 5 }));
    await sync.onLocalChange(frame2);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.id).toBe("m1");
    expect(patches[0]!.patch).toEqual({ x: 130, y: 190 });
    expect(calls.some((c) => c.op === "PATCH" && c.id.startsWith("m1-"))).toBe(false);
  });

  it("moving a placeholder PATCHes geometry only, never payload", async () => {
    const legacy = row({ id: "p1", kind: "user_shape", payload: { tldraw_shape: { type: "image" } } });
    const { api, calls, getElement } = makeApi({ p1: legacy });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const ph = el({
      id: "p1",
      type: "rectangle",
      customData: { taos_id: "p1", taos_kind: "user_shape", taos_placeholder: true },
    });
    sync.markRemote([ph]);
    await sync.onLocalChange([bump(ph, { x: 300, y: 400 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    const patches = PATCHES(calls);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.patch).toEqual({ x: 300, y: 400, w: 100, h: 50, rotation: 0 });
    expect("payload" in patches[0]!.patch).toBe(false);
  });

  it("a 5000-point freedraw is simplified under the payload cap", async () => {
    const { api, calls } = makeApi();
    const sync = createSceneSync({ projectId: "prj", api });
    const points: [number, number][] = [];
    const pressures: number[] = [];
    for (let i = 0; i < 5000; i++) {
      // A noisy sine so RDP has both structure to keep and jitter to drop.
      points.push([i * 0.37 + Math.sin(i / 7) * 0.31, Math.sin(i / 40) * 120 + Math.cos(i / 3) * 0.29]);
      pressures.push(0.5 + (i % 10) / 100);
    }
    const stroke = el({ id: "fd1", type: "freedraw", points, pressures, width: 1850, height: 240 });
    expect(JSON.stringify({ excalidraw_element: stroke }).length).toBeGreaterThan(64 * 1024);
    await sync.onLocalChange([stroke]);
    await settle();
    const posts = POSTS(calls);
    expect(posts).toHaveLength(1);
    const payload = posts[0]!.input.payload as { excalidraw_element: SyncSceneElement };
    const wire = JSON.stringify(payload);
    expect(new TextEncoder().encode(wire).length).toBeLessThanOrEqual(PAYLOAD_SOFT_CAP_BYTES);
    const sent = payload.excalidraw_element;
    expect(sent.points!.length).toBeLessThan(5000);
    expect(sent.points!.length).toBeGreaterThan(2);
    expect(sent.points![0]).toEqual(points[0]);
    expect(sent.points![sent.points!.length - 1]).toEqual(points[4999]);
    expect(sent.pressures!.length).toBe(sent.points!.length);
  });

  it("simplifyFreedraw keeps the endpoints and never returns fewer than two points", () => {
    const pts: [number, number][] = [[0, 0], [1, 0.01], [2, -0.01], [3, 0]];
    const out = simplifyFreedraw(pts, [1, 1, 1, 1], 0.5);
    expect(out.points[0]).toEqual([0, 0]);
    expect(out.points[out.points.length - 1]).toEqual([3, 0]);
    expect(out.points.length).toBeGreaterThanOrEqual(2);
    expect(out.pressures.length).toBe(out.points.length);
  });

  it("a mindmap_edge arrow that moves with its notes emits no write of its own", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}), e1: row({ id: "e1", kind: "mindmap_edge", payload: { from: "n1", to: "n2" } }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    const arrow = el({ id: "e1", type: "arrow", customData: { taos_id: "e1", taos_kind: "mindmap_edge" } });
    sync.markRemote([rect, label, arrow]);
    await sync.onLocalChange([bump(rect, { x: 40 }), label, bump(arrow, { x: 41 })]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls).map((p) => p.id)).toEqual(["n1"]);
  });

  it("a pending local PATCH is dropped when a NEWER remote change lands first (never clobber)", async () => {
    const conflicts: string[] = [];
    const { api, calls, getElement } = makeApi({ n1: row({ updated_at: 5 }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement, onConflict: (id) => conflicts.push(id) });
    const stamped = noteScene().map((e) => ({ ...e, customData: { ...e.customData, taos_updated_at: 5 } }));
    sync.markRemote(stamped);
    await sync.onLocalChange([bump(stamped[0]!, { x: 70 }), stamped[1]!]);
    // Before the trailing edge fires, an agent's update arrives via SSE and the
    // board re-applies the row (updated_at 9) through markRemote.
    const remote = stamped.map((e) => bump(e, { customData: { ...e.customData, taos_updated_at: 9 }, x: 500 }));
    sync.markRemote(remote);
    await sync.onLocalChange(remote);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls)).toHaveLength(0);
    expect(conflicts).toEqual(["n1"]);
  });

  it("our own write echo does not drop a later pending edit", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({ updated_at: 5 }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const stamped = noteScene().map((e) => ({ ...e, customData: { ...e.customData, taos_updated_at: 5 } }));
    sync.markRemote(stamped);
    const moved = bump(stamped[0]!, { x: 70 });
    await sync.onLocalChange([moved, stamped[1]!]);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls)).toHaveLength(1);
    const echoedStamp = getElement("n1")!.updated_at;
    // The user keeps dragging; then the SSE echo of our own PATCH arrives.
    const moved2 = bump(moved, { x: 90 });
    await sync.onLocalChange([moved2, stamped[1]!]);
    const echo = [moved2, stamped[1]!].map((e) => bump(e, { customData: { ...e.customData, taos_updated_at: echoedStamp } }));
    sync.markRemote(echo);
    await sync.onLocalChange(echo);
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(PATCHES(calls)).toHaveLength(2);
    expect(PATCHES(calls)[1]!.patch.x).toBe(90);
  });

  it("does not loop: a PATCH whose SSE echo is re-applied through markRemote + onChange writes once (iteration cap)", async () => {
    // Model the live wiring: every write comes back over SSE, the board rebuilds
    // the scene from the row (new version + nonce, as convertToExcalidrawElements
    // does) and calls markRemote then Excalidraw fires onChange. A sync without
    // echo suppression re-PATCHes on every round and never terminates.
    const { api, calls, getElement } = makeApi({ n1: row({ updated_at: 5 }) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    let scene = noteScene().map((e) => ({ ...e, customData: { ...e.customData, taos_updated_at: 5 } }));
    sync.markRemote(scene);
    await sync.onLocalChange([bump(scene[0]!, { x: 33 }), scene[1]!]);
    const CAP = 25;
    let rounds = 0;
    let seen = 0;
    while (rounds < CAP) {
      await vi.advanceTimersByTimeAsync(200);
      await settle();
      const n = PATCHES(calls).length;
      if (n === seen) break; // quiescent: no new write this round
      seen = n;
      rounds++;
      const r = getElement("n1")!;
      scene = scene.map((e) =>
        bump(e, { x: e.type === "rectangle" ? r.x : e.x, customData: { ...e.customData, taos_updated_at: r.updated_at } }),
      );
      sync.markRemote(scene);
      await sync.onLocalChange(scene);
    }
    expect(rounds).toBeLessThan(CAP);
    expect(PATCHES(calls)).toHaveLength(1);
  });

  it("flush() sends pending PATCHes immediately and dispose() drops them", async () => {
    const { api, calls, getElement } = makeApi({ n1: row({}) });
    const sync = createSceneSync({ projectId: "prj", api, getElement });
    const [rect, label] = noteScene() as [SyncSceneElement, SyncSceneElement];
    sync.markRemote([rect, label]);
    await sync.onLocalChange([bump(rect, { x: 1 }), label]);
    await sync.flush();
    expect(PATCHES(calls)).toHaveLength(1);
    await sync.onLocalChange([bump(rect, { x: 2 }), label]);
    sync.dispose();
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(PATCHES(calls)).toHaveLength(1);
  });

  it("module has no runtime import of @excalidraw/excalidraw (types only)", async () => {
    const src = (await import("../canvas/excalidraw-sync?raw")).default as string;
    const runtimeImports = src
      .split("\n")
      .filter((l) => /^\s*import\s/.test(l) && !/^\s*import\s+type\s/.test(l) && /@excalidraw/.test(l));
    expect(runtimeImports).toEqual([]);
  });
});
