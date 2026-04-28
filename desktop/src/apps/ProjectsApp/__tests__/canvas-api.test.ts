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
});
