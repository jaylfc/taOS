import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchMemoryModel, setMemoryModel } from "./memory-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchMemoryModel", () => {
  it("calls GET /api/memory/model and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ model: "gpt-4o-mini", supported: true }),
    });
    global.fetch = fetchMock;

    const result = await fetchMemoryModel();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/model");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toEqual({ model: "gpt-4o-mini", supported: true });
  });

  it("returns model as null when none is set", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ model: null, supported: false }),
    });

    const result = await fetchMemoryModel();
    expect(result.model).toBeNull();
    expect(result.supported).toBe(false);
  });

  it("throws on non-ok response with detail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "internal error" }),
    });

    await expect(fetchMemoryModel()).rejects.toThrow("internal error");
  });

  it("throws on non-ok response with error field", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "bad request" }),
    });

    await expect(fetchMemoryModel()).rejects.toThrow("bad request");
  });

  it("throws with status when body has no detail or error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({}),
    });

    await expect(fetchMemoryModel()).rejects.toThrow("Request failed (503)");
  });
});

describe("setMemoryModel", () => {
  it("calls PUT /api/memory/model with body and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ model: "gpt-4o" }),
    });
    global.fetch = fetchMock;

    const result = await setMemoryModel({ model: "gpt-4o" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/model");
    expect(opts.method).toBe("PUT");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.model).toBe("gpt-4o");
    expect(result).toEqual({ model: "gpt-4o" });
  });

  it("sends clear flag when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ model: null }),
    });
    global.fetch = fetchMock;

    const result = await setMemoryModel({ clear: true });

    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.clear).toBe(true);
    expect(result.model).toBeNull();
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "invalid model" }),
    });

    await expect(setMemoryModel({ model: "bad" })).rejects.toThrow("invalid model");
  });
});
