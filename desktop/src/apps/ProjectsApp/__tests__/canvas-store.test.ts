import { describe, it, expect } from "vitest";
import { createCanvasStore } from "../canvas/canvas-store";

describe("canvas-store", () => {
  it("seeds elements", () => {
    const store = createCanvasStore();
    store.getState().seed([
      { id: "cve-1", kind: "note", x: 0, y: 0, w: 1, h: 1,
        rotation: 0, z_index: 0, payload: {}, project_id: "p",
        author_kind: "user", author_id: "u",
        created_at: 0, updated_at: 0, deleted_at: null } as any,
    ]);
    expect(store.getState().elements["cve-1"].kind).toBe("note");
  });

  it("upsert replaces by id", () => {
    const store = createCanvasStore();
    store.getState().upsert({ id: "x", x: 1 } as any);
    store.getState().upsert({ id: "x", x: 99 } as any);
    expect(store.getState().elements["x"].x).toBe(99);
  });

  it("remove drops the element", () => {
    const store = createCanvasStore();
    store.getState().upsert({ id: "x" } as any);
    store.getState().remove("x");
    expect(store.getState().elements["x"]).toBeUndefined();
  });
});
