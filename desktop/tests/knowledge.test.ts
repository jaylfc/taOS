import { describe, it, expect, beforeEach, vi } from "vitest";
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
} from "../src/lib/knowledge";
import type { KnowledgeItem, Snapshot, CategoryRule, AgentSubscription } from "../src/lib/knowledge";

const MOCK_ITEM: KnowledgeItem = {
  id: "abc-123",
  source_type: "article",
  source_url: "https://example.com/article",
  source_id: null,
  title: "Test Article",
  author: "Test Author",
  summary: "A test summary.",
  content: "Full content here.",
  media_path: null,
  thumbnail: null,
  categories: ["Development"],
  tags: [],
  metadata: {},
  status: "ready",
  monitor: {},
  created_at: 1700000000,
  updated_at: 1700000000,
};

const MOCK_SNAPSHOT: Snapshot = {
  id: 1,
  item_id: "abc-123",
  snapshot_at: 1700000000,
  content_hash: "deadbeef",
  diff_json: { changed: false, old_hash: "aa", new_hash: "bb" },
  metadata_json: {},
};

const MOCK_RULE: CategoryRule = {
  id: 1,
  pattern: "LocalLLaMA",
  match_on: "subreddit",
  category: "AI/ML",
  priority: 10,
};

const MOCK_SUBSCRIPTION: AgentSubscription = {
  agent_name: "research-agent",
  category: "AI/ML",
  auto_ingest: false,
};

function mockFetchJson(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(data),
  });
}

function mockFetchFail() {
  return vi.fn().mockRejectedValue(new Error("network error"));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("listItems", () => {
  it("returns items from the API", async () => {
    globalThis.fetch = mockFetchJson({ items: [MOCK_ITEM], count: 1 });
    const result = await listItems();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].title).toBe("Test Article");
    expect(result.count).toBe(1);
  });

  it("passes filter params", async () => {
    globalThis.fetch = mockFetchJson({ items: [], count: 0 });
    await listItems({ source_type: "reddit", category: "AI/ML", status: "ready", limit: 10, offset: 20 });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("source_type=reddit");
    expect(url).toContain("category=AI%2FML");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=20");
  });

  it("returns empty on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await listItems();
    expect(result.items).toEqual([]);
    expect(result.count).toBe(0);
  });
});

describe("getItem", () => {
  it("returns a single item", async () => {
    globalThis.fetch = mockFetchJson(MOCK_ITEM);
    const result = await getItem("abc-123");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("abc-123");
  });

  it("returns null on 404", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await getItem("bad-id");
    expect(result).toBeNull();
  });
});

describe("deleteItem", () => {
  it("returns true on success", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted", id: "abc-123" });
    const result = await deleteItem("abc-123");
    expect(result).toBe(true);
  });

  it("returns false on 404", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await deleteItem("bad-id");
    expect(result).toBe(false);
  });
});

describe("searchItems", () => {
  it("returns keyword results", async () => {
    globalThis.fetch = mockFetchJson({ results: [MOCK_ITEM], mode: "keyword" });
    const result = await searchItems("test", "keyword");
    expect(result.results).toHaveLength(1);
    expect(result.mode).toBe("keyword");
  });

  it("passes semantic mode", async () => {
    globalThis.fetch = mockFetchJson({ results: [], mode: "semantic" });
    await searchItems("test", "semantic", 10);
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("mode=semantic");
    expect(url).toContain("limit=10");
  });
});

describe("ingestUrl", () => {
  it("posts URL and returns id", async () => {
    globalThis.fetch = mockFetchJson({ id: "new-123", status: "pending" });
    const result = await ingestUrl("https://example.com");
    expect(result).toEqual({ id: "new-123", status: "pending" });
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await ingestUrl("https://example.com");
    expect(result).toBeNull();
  });
});

describe("listSnapshots", () => {
  it("returns snapshots", async () => {
    globalThis.fetch = mockFetchJson({ snapshots: [MOCK_SNAPSHOT] });
    const result = await listSnapshots("abc-123");
    expect(result).toHaveLength(1);
    expect(result[0].content_hash).toBe("deadbeef");
  });
});

describe("listRules", () => {
  it("returns rules", async () => {
    globalThis.fetch = mockFetchJson({ rules: [MOCK_RULE] });
    const result = await listRules();
    expect(result).toHaveLength(1);
    expect(result[0].pattern).toBe("LocalLLaMA");
  });
});

describe("createRule", () => {
  it("posts rule and returns id", async () => {
    globalThis.fetch = mockFetchJson({ id: 2, status: "created" });
    const result = await createRule({ pattern: "test*", match_on: "title", category: "Test", priority: 0 });
    expect(result).toBe(2);
  });
});

describe("deleteRule", () => {
  it("returns true on success", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted", id: 1 });
    const result = await deleteRule(1);
    expect(result).toBe(true);
  });
});

describe("listSubscriptions", () => {
  it("returns subscriptions", async () => {
    globalThis.fetch = mockFetchJson({ subscriptions: [MOCK_SUBSCRIPTION] });
    const result = await listSubscriptions();
    expect(result).toHaveLength(1);
    expect(result[0].agent_name).toBe("research-agent");
  });

  it("filters by agent name", async () => {
    globalThis.fetch = mockFetchJson({ subscriptions: [] });
    await listSubscriptions("research-agent");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("agent_name=research-agent");
  });
});

describe("setSubscription", () => {
  it("posts subscription", async () => {
    globalThis.fetch = mockFetchJson({ status: "ok" });
    const result = await setSubscription({ agent_name: "test", category: "AI/ML", auto_ingest: true });
    expect(result).toBe(true);
  });
});

describe("deleteSubscription", () => {
  it("returns true on success", async () => {
    globalThis.fetch = mockFetchJson({ status: "deleted" });
    const result = await deleteSubscription("test", "AI/ML");
    expect(result).toBe(true);
  });
});
