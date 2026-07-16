import { describe, it, expect, vi, beforeEach } from "vitest";
import { canvasApi } from "../canvas/canvas-api";

beforeEach(() => {
  global.fetch = vi.fn();
});

describe("canvasApi", () => {
  it("listElements GETs the right URL", async () => {
    (fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ elements: [] }),
    });
    const r = await canvasApi.listElements("prj-1");
    expect(fetch).toHaveBeenCalledWith("/api/projects/prj-1/canvas/elements");
    expect(r).toEqual([]);
  });

  it("addElement POSTs body", async () => {
    (fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ element: { id: "cve-1", kind: "note" } }),
    });
    const r = await canvasApi.addElement("prj-1", {
      kind: "note", x: 1, y: 2, w: 3, h: 4, payload: { text: "x" },
    });
    expect(r.id).toBe("cve-1");
    const call = (fetch as any).mock.calls[0];
    expect(call[0]).toBe("/api/projects/prj-1/canvas/elements");
    expect(call[1].method).toBe("POST");
  });

  it("deleteElement returns true on 204", async () => {
    (fetch as any).mockResolvedValue({ ok: true, status: 204 });
    const r = await canvasApi.deleteElement("prj-1", "cve-1");
    expect(r).toBe(true);
  });

  it("listElements appends element_id query when scoped", async () => {
    (fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ elements: [] }),
    });
    await canvasApi.listElements("prj-1", "elm-1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/prj-1/canvas/elements?element_id=elm-1",
    );
  });

  it("listElements omits the query when not scoped", async () => {
    (fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ elements: [] }),
    });
    await canvasApi.listElements("prj-1");
    expect(fetch).toHaveBeenCalledWith("/api/projects/prj-1/canvas/elements");
  });

  it("addElement sends element_id when provided", async () => {
    (fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ element: { id: "cve-1", kind: "note" } }),
    });
    await canvasApi.addElement("prj-1", {
      kind: "note", x: 1, y: 2, w: 3, h: 4, payload: { text: "x" },
      element_id: "elm-1",
    });
    const call = (fetch as any).mock.calls[0];
    const body = JSON.parse(call[1].body);
    expect(body.element_id).toBe("elm-1");
  });
});
