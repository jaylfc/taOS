import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchMemoryStats,
  fetchBackendCapabilities,
  fetchSettingsSchema,
  fetchMemorySettings,
  updateMemorySettings,
  fetchCatalogDate,
  fetchCatalogSession,
  fetchCatalogSessionContext,
  triggerCatalogIndex,
  fetchCatalogSearch,
  fetchCatalogStats,
  fetchAgentMemoryConfig,
  updateAgentMemoryConfig,
} from "./memory";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchMemoryStats", () => {
  it("calls GET /api/memory/stats and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ total: 42 }),
    });
    global.fetch = fetchMock;

    const result = await fetchMemoryStats();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/stats");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toEqual({ total: 42 });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchMemoryStats()).toEqual({});
  });
});

describe("fetchBackendCapabilities", () => {
  it("calls GET /api/memory/backend/capabilities and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ name: "openai", version: "1", capabilities: ["embed"] }),
    });
    global.fetch = fetchMock;

    const result = await fetchBackendCapabilities();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/backend/capabilities");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toEqual({ name: "openai", version: "1", capabilities: ["embed"] });
  });

  it("returns fallback on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    const result = await fetchBackendCapabilities();
    expect(result).toEqual({ name: "unknown", version: "0", capabilities: [] });
  });
});

describe("fetchSettingsSchema", () => {
  it("calls GET /api/memory/backend/settings-schema and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ fields: [{ key: "model", type: "string" }] }),
    });
    global.fetch = fetchMock;

    const result = await fetchSettingsSchema();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/backend/settings-schema");
    expect(result).toEqual({ fields: [{ key: "model", type: "string" }] });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchSettingsSchema()).toEqual({});
  });
});

describe("fetchMemorySettings", () => {
  it("calls GET /api/memory/settings and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ model: "gpt-4o" }),
    });
    global.fetch = fetchMock;

    const result = await fetchMemorySettings();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/settings");
    expect(result).toEqual({ model: "gpt-4o" });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchMemorySettings()).toEqual({});
  });
});

describe("updateMemorySettings", () => {
  it("calls PUT /api/memory/settings with body and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ model: "gpt-4o" }),
    });
    global.fetch = fetchMock;

    const result = await updateMemorySettings({ model: "gpt-4o" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/settings");
    expect(opts.method).toBe("PUT");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.model).toBe("gpt-4o");
    expect(result).toEqual({ model: "gpt-4o" });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await updateMemorySettings({ model: "gpt-4o" })).toEqual({});
  });
});

describe("fetchCatalogDate", () => {
  it("calls GET /api/memory/catalog/date/:date and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => [{ id: 1, topic: "test" }],
    });
    global.fetch = fetchMock;

    const result = await fetchCatalogDate("2025-01-01");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/date/2025-01-01");
    expect(result).toEqual([{ id: 1, topic: "test" }]);
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchCatalogDate("2025-01-01")).toEqual([]);
  });
});

describe("fetchCatalogSession", () => {
  it("calls GET /api/memory/catalog/session/:id and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: 1, topic: "test" }),
    });
    global.fetch = fetchMock;

    const result = await fetchCatalogSession(1);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/session/1");
    expect(result).toEqual({ id: 1, topic: "test" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchCatalogSession(1)).toBeNull();
  });
});

describe("fetchCatalogSessionContext", () => {
  it("calls GET /api/memory/catalog/session/:id/context and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ context: "some context" }),
    });
    global.fetch = fetchMock;

    const result = await fetchCatalogSessionContext(5);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/session/5/context");
    expect(result).toEqual({ context: "some context" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchCatalogSessionContext(5)).toBeNull();
  });
});

describe("triggerCatalogIndex", () => {
  it("calls POST /api/memory/catalog/index with body and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ indexed: true }),
    });
    global.fetch = fetchMock;

    const body = { date: "2025-01-01", force: true };
    const result = await triggerCatalogIndex(body);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/index");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const sentBody = JSON.parse(opts.body);
    expect(sentBody.date).toBe("2025-01-01");
    expect(sentBody.force).toBe(true);
    expect(result).toEqual({ indexed: true });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await triggerCatalogIndex({ date: "2025-01-01" })).toEqual({});
  });
});

describe("fetchCatalogSearch", () => {
  it("calls GET /api/memory/catalog/search with encoded query and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => [{ id: 1, topic: "hello world" }],
    });
    global.fetch = fetchMock;

    const result = await fetchCatalogSearch("hello world");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/search?q=hello%20world");
    expect(result).toEqual([{ id: 1, topic: "hello world" }]);
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchCatalogSearch("test")).toEqual([]);
  });
});

describe("fetchCatalogStats", () => {
  it("calls GET /api/memory/catalog/stats and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ sessions: 10 }),
    });
    global.fetch = fetchMock;

    const result = await fetchCatalogStats();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/catalog/stats");
    expect(result).toEqual({ sessions: 10 });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchCatalogStats()).toEqual({});
  });
});

describe("fetchAgentMemoryConfig", () => {
  it("calls GET /api/agents/:name/memory-config and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ auto_recall: true }),
    });
    global.fetch = fetchMock;

    const result = await fetchAgentMemoryConfig("agent-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/agents/agent-1/memory-config");
    expect(result).toEqual({ auto_recall: true });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchAgentMemoryConfig("agent-1")).toEqual({});
  });
});

describe("updateAgentMemoryConfig", () => {
  it("calls PUT /api/agents/:name/memory-config with body and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ auto_recall: false }),
    });
    global.fetch = fetchMock;

    const result = await updateAgentMemoryConfig("agent-1", { auto_recall: false });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/agents/agent-1/memory-config");
    expect(opts.method).toBe("PUT");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.auto_recall).toBe(false);
    expect(result).toEqual({ auto_recall: false });
  });

  it("returns {} on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await updateAgentMemoryConfig("agent-1", { auto_recall: true })).toEqual({});
  });
});
