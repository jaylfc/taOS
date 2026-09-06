import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listItems,
  getItem,
  deleteItem,
  searchItems,
  ingestUrl,
  listSnapshots,
  listRules,
  createRule,
  deleteRule,
  listSubscriptions,
  setSubscription,
  deleteSubscription,
} from "./knowledge";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listItems", () => {
  it("calls GET /api/knowledge/items and returns items + count", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        items: [
          { id: "ki-1", title: "First", source_type: "web" },
          { id: "ki-2", title: "Second", source_type: "web" },
        ],
        count: 2,
      }),
    });
    global.fetch = fetchMock;

    const result = await listItems();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/items");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result.items).toHaveLength(2);
    expect(result.items[0].id).toBe("ki-1");
    expect(result.count).toBe(2);
  });

  it("returns empty items on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    const result = await listItems();
    expect(result).toEqual({ items: [], count: 0 });
  });

  it("returns empty items on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await listItems();
    expect(result).toEqual({ items: [], count: 0 });
  });

  it("builds query params from ListItemsParams", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ items: [], count: 0 }),
    });
    global.fetch = fetchMock;

    await listItems({ source_type: "web", status: "active", category: "tech", limit: 10, offset: 5 });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("source_type=web");
    expect(url).toContain("status=active");
    expect(url).toContain("category=tech");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=5");
  });

  it("returns empty items when data.items is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ items: null, count: 0 }),
    });
    const result = await listItems();
    expect(result.items).toEqual([]);
  });
});

describe("getItem", () => {
  it("calls GET /api/knowledge/items/:id and returns parsed object", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "ki-1", title: "Hello", source_type: "web" }),
    });
    global.fetch = fetchMock;

    const result = await getItem("ki-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/items/ki-1");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toEqual({ id: "ki-1", title: "Hello", source_type: "web" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await getItem("ki-1")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getItem("ki-1")).toBeNull();
  });

  it("encodes id in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "ki/a" }),
    });
    global.fetch = fetchMock;
    await getItem("ki/a");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/items/ki%2Fa");
  });
});

describe("deleteItem", () => {
  it("calls DELETE /api/knowledge/items/:id and returns true on ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    const result = await deleteItem("ki-1");

    expect(result).toBe(true);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/items/ki-1");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await deleteItem("ki-1")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await deleteItem("ki-1")).toBe(false);
  });
});

describe("searchItems", () => {
  it("calls GET /api/knowledge/search with q, mode, limit and returns results", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        results: [{ id: "ki-1", title: "Match" }],
        mode: "keyword",
      }),
    });
    global.fetch = fetchMock;

    const result = await searchItems("hello world", "keyword", 10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("q=hello+world");
    expect(url).toContain("mode=keyword");
    expect(url).toContain("limit=10");
    expect(result.results).toHaveLength(1);
    expect(result.results[0].id).toBe("ki-1");
    expect(result.mode).toBe("keyword");
  });

  it("returns empty results on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    const result = await searchItems("test");
    expect(result.results).toEqual([]);
  });

  it("returns empty results on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await searchItems("test");
    expect(result.results).toEqual([]);
  });

  it("uses default mode=keyword and limit=20", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ results: [], mode: "keyword" }),
    });
    global.fetch = fetchMock;
    await searchItems("test");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("mode=keyword");
    expect(url).toContain("limit=20");
  });
});

describe("ingestUrl", () => {
  it("calls POST /api/knowledge/ingest with body and returns parsed result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "ki-new", status: "pending" }),
    });
    global.fetch = fetchMock;

    const result = await ingestUrl("https://example.com/article", {
      title: "Example",
      text: "Some text",
      categories: ["tech"],
      source: "library",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/ingest");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.url).toBe("https://example.com/article");
    expect(body.title).toBe("Example");
    expect(body.text).toBe("Some text");
    expect(body.categories).toEqual(["tech"]);
    expect(body.source).toBe("library");
    expect(result).toEqual({ id: "ki-new", status: "pending" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 400 });
    expect(await ingestUrl("https://example.com")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await ingestUrl("https://example.com")).toBeNull();
  });

  it("uses default values for missing opts fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "ki-1" }),
    });
    global.fetch = fetchMock;
    await ingestUrl("https://example.com");
    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.title).toBe("");
    expect(body.text).toBe("");
    expect(body.categories).toEqual([]);
    expect(body.source).toBe("library");
  });
});

describe("listSnapshots", () => {
  it("calls GET /api/knowledge/items/:id/snapshots and returns snapshots array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        snapshots: [
          { id: 1, item_id: "ki-1", content_hash: "abc" },
          { id: 2, item_id: "ki-1", content_hash: "def" },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await listSnapshots("ki-1", 10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/items/ki-1/snapshots?limit=10");
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe(1);
  });

  it("returns empty array on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    const result = await listSnapshots("ki-1");
    expect(result).toEqual([]);
  });

  it("returns empty array on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await listSnapshots("ki-1");
    expect(result).toEqual([]);
  });

  it("uses default limit=20", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ snapshots: [] }),
    });
    global.fetch = fetchMock;
    await listSnapshots("ki-1");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("limit=20");
  });
});

describe("listRules", () => {
  it("calls GET /api/knowledge/rules and returns rules array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        rules: [
          { id: 1, pattern: "github.com", match_on: "url", category: "dev", priority: 1 },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await listRules();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/rules");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
    expect(result[0].pattern).toBe("github.com");
  });

  it("returns empty array on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await listRules()).toEqual([]);
  });

  it("returns empty array on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await listRules()).toEqual([]);
  });
});

describe("createRule", () => {
  it("calls POST /api/knowledge/rules with body and returns id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: 42 }),
    });
    global.fetch = fetchMock;

    const result = await createRule({
      pattern: "github.com",
      match_on: "url",
      category: "dev",
      priority: 1,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/rules");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.pattern).toBe("github.com");
    expect(body.match_on).toBe("url");
    expect(body.category).toBe("dev");
    expect(body.priority).toBe(1);
    expect(result).toBe(42);
  });

  it("returns null when response has no id", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({}),
    });
    expect(await createRule({ pattern: "x", match_on: "url", category: "y", priority: 0 })).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await createRule({ pattern: "x", match_on: "url", category: "y", priority: 0 })).toBeNull();
  });
});

describe("deleteRule", () => {
  it("calls DELETE /api/knowledge/rules/:id and returns true on ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    const result = await deleteRule(5);

    expect(result).toBe(true);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/rules/5");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await deleteRule(5)).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await deleteRule(5)).toBe(false);
  });
});

describe("listSubscriptions", () => {
  it("calls GET /api/knowledge/subscriptions and returns subscriptions array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        subscriptions: [
          { agent_name: "agent-1", category: "dev", auto_ingest: true },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await listSubscriptions();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/subscriptions");
    expect(result).toHaveLength(1);
    expect(result[0].agent_name).toBe("agent-1");
  });

  it("includes agent_name as query param when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ subscriptions: [] }),
    });
    global.fetch = fetchMock;
    await listSubscriptions("my-agent");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("agent_name=my-agent");
  });

  it("returns empty array on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await listSubscriptions()).toEqual([]);
  });

  it("returns empty array on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await listSubscriptions()).toEqual([]);
  });
});

describe("setSubscription", () => {
  it("calls POST /api/knowledge/subscriptions with body and returns true when status=ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "ok" }),
    });
    global.fetch = fetchMock;

    const result = await setSubscription({
      agent_name: "agent-1",
      category: "dev",
      auto_ingest: true,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/subscriptions");
    expect(opts.method).toBe("POST");
    expect((opts.headers?.get?.("Content-Type") ?? opts.headers?.["Content-Type"])).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.agent_name).toBe("agent-1");
    expect(body.category).toBe("dev");
    expect(body.auto_ingest).toBe(true);
    expect(result).toBe(true);
  });

  it("returns false when response status is not ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "error" }),
    });
    expect(
      await setSubscription({ agent_name: "a", category: "b", auto_ingest: false }),
    ).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(
      await setSubscription({ agent_name: "a", category: "b", auto_ingest: false }),
    ).toBe(false);
  });
});

describe("deleteSubscription", () => {
  it("calls DELETE /api/knowledge/subscriptions/:agent/:category and returns true on ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    const result = await deleteSubscription("agent-1", "dev");

    expect(result).toBe(true);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/subscriptions/agent-1/dev");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await deleteSubscription("agent-1", "dev")).toBe(false);
  });

  it("returns false on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await deleteSubscription("agent-1", "dev")).toBe(false);
  });

  it("encodes agent_name and category in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;
    await deleteSubscription("my agent", "dev ops");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/knowledge/subscriptions/my%20agent/dev%20ops");
  });
});
