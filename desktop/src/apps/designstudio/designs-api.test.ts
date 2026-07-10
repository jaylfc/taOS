import { describe, it, expect, vi, afterEach } from "vitest";
import {
  createDesign,
  deleteDesign,
  getDesign,
  listDesigns,
  renameDesign,
  updateDesign,
} from "./designs-api";

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

describe("designs-api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listDesigns GETs /api/designs and returns the array", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse([{ id: "d1", name: "A", updated_at: 2 }])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const designs = await listDesigns();
    expect(fetchMock).toHaveBeenCalledWith("/api/designs", { credentials: "include" });
    expect(designs).toEqual([{ id: "d1", name: "A", updated_at: 2 }]);
  });

  it("listDesigns throws a friendly error on a failed request", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))));
    await expect(listDesigns()).rejects.toThrow(/could not load designs/i);
  });

  it("getDesign fetches a single record by (encoded) id", async () => {
    const doc = { id: "d 1", name: "A", content: "{}" };
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(doc)));
    vi.stubGlobal("fetch", fetchMock);

    const got = await getDesign("d 1");
    expect(fetchMock).toHaveBeenCalledWith("/api/designs/d%201", { credentials: "include" });
    expect(got).toEqual(doc);
  });

  it("getDesign throws when the record can't be opened", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 404))));
    await expect(getDesign("nope")).rejects.toThrow(/could not open design/i);
  });

  it("createDesign POSTs name + content and returns the saved doc", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/designs");
      expect(init?.method).toBe("POST");
      const body = JSON.parse(String(init?.body));
      expect(body).toEqual({ name: "Poster", content: "{}" });
      return Promise.resolve(jsonResponse({ id: "d1", name: "Poster", content: "{}" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const saved = await createDesign("Poster", "{}");
    expect(saved.id).toBe("d1");
  });

  it("createDesign surfaces the server's error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ error: "name is required" }, false, 400))),
    );
    await expect(createDesign("", "{}")).rejects.toThrow("name is required");
  });

  it("createDesign falls back to a generic message when the body has no error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 413))));
    await expect(createDesign("x", "{}")).rejects.toThrow(/save failed/i);
  });

  it("updateDesign PUTs only the provided fields", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/designs/d1");
      expect(init?.method).toBe("PUT");
      const body = JSON.parse(String(init?.body));
      // name was undefined, so it must be omitted from the payload.
      expect(body).toEqual({ content: "{}" });
      return Promise.resolve(jsonResponse({ id: "d1", name: "A", content: "{}" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    await updateDesign("d1", undefined, "{}");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renameDesign PUTs just the name", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body).toEqual({ name: "New" });
      return Promise.resolve(jsonResponse({ id: "d1", name: "New", content: "{}" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const renamed = await renameDesign("d1", "New");
    expect(renamed.name).toBe("New");
  });

  it("deleteDesign DELETEs the record", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/designs/d1");
      expect(init?.method).toBe("DELETE");
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    await deleteDesign("d1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("deleteDesign throws on failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))));
    await expect(deleteDesign("d1")).rejects.toThrow(/delete failed/i);
  });
});
