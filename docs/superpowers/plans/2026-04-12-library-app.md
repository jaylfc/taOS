# Library App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the unified Library App frontend for browsing, searching, organising, and monitoring all content in the Knowledge Base.

**Architecture:** Single `LibraryApp.tsx` with internal view state (`list` | `detail`), a `lib/knowledge.ts` API helper module, and registration in `app-registry.ts`. Follows existing MemoryApp/AgentsApp patterns: `useCallback`+`useEffect` data fetching, `isMobile` runtime check for responsive layout, barrel imports from `@/components/ui`.

**Tech Stack:** React, TypeScript, Tailwind CSS, Vitest, lucide-react icons, shadcn-style UI components

**Spec:** `docs/superpowers/specs/2026-04-12-library-app-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `desktop/src/lib/knowledge.ts` | TypeScript types + typed fetch wrappers for all `/api/knowledge/*` endpoints |
| Create | `desktop/tests/knowledge.test.ts` | Unit tests for API helpers (mocked fetch) |
| Create | `desktop/src/apps/LibraryApp.tsx` | Main app component — sidebar, list view, detail view, category management, monitoring |
| Modify | `desktop/src/registry/app-registry.ts` | Register library app entry |
| Modify | `tinyagentos/knowledge_monitor.py` | Change decay to floor at 30 days instead of stopping |
| Modify | `tests/test_knowledge_monitor.py` | Update tests for new floor behaviour |

---

## Task 1: TypeScript Types and API Helpers

**Files:**
- Create: `desktop/src/lib/knowledge.ts`
- Create: `desktop/tests/knowledge.test.ts`

- [ ] **Step 1: Write failing tests for API helpers**

Create `desktop/tests/knowledge.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/knowledge.test.ts`
Expected: FAIL — module `../src/lib/knowledge` does not exist

- [ ] **Step 3: Implement types and API helpers**

Create `desktop/src/lib/knowledge.ts`:

```ts
/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface MonitorConfig {
  frequency?: number;
  current_interval?: number;
  decay_rate?: number;
  stop_after_days?: number;
  pinned?: boolean;
  last_poll?: number;
  last_hash?: string;
}

export interface KnowledgeItem {
  id: string;
  source_type: string;
  source_url: string;
  source_id: string | null;
  title: string;
  author: string;
  summary: string;
  content: string;
  media_path: string | null;
  thumbnail: string | null;
  categories: string[];
  tags: string[];
  metadata: Record<string, unknown>;
  status: string;
  monitor: MonitorConfig;
  created_at: number;
  updated_at: number;
}

export interface Snapshot {
  id: number;
  item_id: string;
  snapshot_at: number;
  content_hash: string;
  diff_json: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
}

export interface CategoryRule {
  id: number;
  pattern: string;
  match_on: string;
  category: string;
  priority: number;
}

export interface AgentSubscription {
  agent_name: string;
  category: string;
  auto_ingest: boolean;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(url, { ...init, headers: { Accept: "application/json", ...init?.headers } });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

async function postJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ------------------------------------------------------------------ */
/*  Items                                                              */
/* ------------------------------------------------------------------ */

export interface ListItemsParams {
  source_type?: string;
  status?: string;
  category?: string;
  limit?: number;
  offset?: number;
}

export async function listItems(params?: ListItemsParams): Promise<{ items: KnowledgeItem[]; count: number }> {
  const qs = new URLSearchParams();
  if (params?.source_type) qs.set("source_type", params.source_type);
  if (params?.status) qs.set("status", params.status);
  if (params?.category) qs.set("category", params.category);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  const url = `/api/knowledge/items${query ? `?${query}` : ""}`;
  const data = await fetchJson<{ items: KnowledgeItem[]; count: number }>(url, { items: [], count: 0 });
  return { items: Array.isArray(data.items) ? data.items : [], count: data.count ?? 0 };
}

export async function getItem(id: string): Promise<KnowledgeItem | null> {
  try {
    const res = await fetch(`/api/knowledge/items/${encodeURIComponent(id)}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function deleteItem(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/knowledge/items/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Search                                                             */
/* ------------------------------------------------------------------ */

export async function searchItems(
  query: string,
  mode: "keyword" | "semantic" = "keyword",
  limit = 20,
): Promise<{ results: KnowledgeItem[]; mode: string }> {
  const qs = new URLSearchParams({ q: query, mode, limit: String(limit) });
  const data = await fetchJson<{ results: KnowledgeItem[]; mode: string }>(
    `/api/knowledge/search?${qs}`,
    { results: [], mode },
  );
  return { results: Array.isArray(data.results) ? data.results : [], mode: data.mode ?? mode };
}

/* ------------------------------------------------------------------ */
/*  Ingest                                                             */
/* ------------------------------------------------------------------ */

export interface IngestOptions {
  title?: string;
  text?: string;
  categories?: string[];
  source?: string;
}

export async function ingestUrl(
  url: string,
  opts?: IngestOptions,
): Promise<{ id: string; status: string } | null> {
  try {
    const res = await fetch("/api/knowledge/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        url,
        title: opts?.title ?? "",
        text: opts?.text ?? "",
        categories: opts?.categories ?? [],
        source: opts?.source ?? "library",
      }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Snapshots                                                          */
/* ------------------------------------------------------------------ */

export async function listSnapshots(itemId: string, limit = 20): Promise<Snapshot[]> {
  const data = await fetchJson<{ snapshots: Snapshot[] }>(
    `/api/knowledge/items/${encodeURIComponent(itemId)}/snapshots?limit=${limit}`,
    { snapshots: [] },
  );
  return Array.isArray(data.snapshots) ? data.snapshots : [];
}

/* ------------------------------------------------------------------ */
/*  Category rules                                                     */
/* ------------------------------------------------------------------ */

export async function listRules(): Promise<CategoryRule[]> {
  const data = await fetchJson<{ rules: CategoryRule[] }>("/api/knowledge/rules", { rules: [] });
  return Array.isArray(data.rules) ? data.rules : [];
}

export interface CreateRuleParams {
  pattern: string;
  match_on: string;
  category: string;
  priority: number;
}

export async function createRule(rule: CreateRuleParams): Promise<number | null> {
  const data = await postJson<{ id?: number }>("/api/knowledge/rules", rule, {});
  return data.id ?? null;
}

export async function deleteRule(id: number): Promise<boolean> {
  try {
    const res = await fetch(`/api/knowledge/rules/${id}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Agent subscriptions                                                */
/* ------------------------------------------------------------------ */

export async function listSubscriptions(agentName?: string): Promise<AgentSubscription[]> {
  const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : "";
  const data = await fetchJson<{ subscriptions: AgentSubscription[] }>(
    `/api/knowledge/subscriptions${qs}`,
    { subscriptions: [] },
  );
  return Array.isArray(data.subscriptions) ? data.subscriptions : [];
}

export async function setSubscription(sub: AgentSubscription): Promise<boolean> {
  const data = await postJson<{ status?: string }>("/api/knowledge/subscriptions", sub, {});
  return data.status === "ok";
}

export async function deleteSubscription(agentName: string, category: string): Promise<boolean> {
  try {
    const res = await fetch(
      `/api/knowledge/subscriptions/${encodeURIComponent(agentName)}/${encodeURIComponent(category)}`,
      { method: "DELETE", headers: { Accept: "application/json" } },
    );
    return res.ok;
  } catch {
    return false;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/knowledge.test.ts`
Expected: all 16 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add desktop/src/lib/knowledge.ts desktop/tests/knowledge.test.ts
git commit -m "feat(library): add knowledge API types and helpers with tests"
```

---

## Task 2: LibraryApp Component and Registration

**Files:**
- Create: `desktop/src/apps/LibraryApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts:32` (after the `images` entry)

The full component is large but ships as one file following the existing convention. It contains: sidebar with four filter sections (sources, categories, status, monitoring), item list with search and sort, detail view with content/history/metadata tabs, category management dialog with rules editor, and contextual agent subscription UI.

- [ ] **Step 1: Create the LibraryApp component**

Create `desktop/src/apps/LibraryApp.tsx` with the full implementation. The component structure:

```tsx
import { useState, useEffect, useCallback } from "react";
import {
  BookOpen, Search, Trash2, ChevronLeft, FolderOpen, ExternalLink,
  RefreshCw, Download, Settings2, Activity, Clock, AlertCircle,
} from "lucide-react";
import {
  Button, Card, CardHeader, CardContent, Input,
  Tabs, TabsList, TabsTrigger, TabsContent,
} from "@/components/ui";
import {
  listItems, getItem, deleteItem, searchItems, listSnapshots,
  listRules, createRule, deleteRule, listSubscriptions,
  setSubscription, deleteSubscription, ingestUrl,
} from "@/lib/knowledge";
import type {
  KnowledgeItem, Snapshot, CategoryRule, AgentSubscription, ListItemsParams,
} from "@/lib/knowledge";

/* -- Types -- */

type View = "list" | "detail";
type SearchMode = "keyword" | "semantic";
type SortMode = "newest" | "updated" | "alpha";
type MonitorFilter = "recent" | "active" | "slow" | null;

interface SidebarFilters {
  source_type: string | null;
  category: string | null;
  status: string | null;
  monitor: MonitorFilter;
}

const SOURCE_TYPES = ["reddit", "youtube", "github", "x", "article", "file", "manual"] as const;
const STATUS_OPTIONS = ["ready", "processing", "error"] as const;
const SOURCE_LABELS: Record<string, string> = {
  reddit: "Reddit", youtube: "YouTube", github: "GitHub", x: "X",
  article: "Articles", file: "Files", manual: "Manual",
};

export function LibraryApp({ windowId: _windowId }: { windowId: string }) {
  /* -- View state -- */
  const [view, setView] = useState<View>("list");
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  /* -- Data state -- */
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [rules, setRules] = useState<CategoryRule[]>([]);
  const [subscriptions, setSubscriptions] = useState<AgentSubscription[]>([]);
  const [agents, setAgents] = useState<{ name: string; color: string }[]>([]);
  const [categories, setCategories] = useState<string[]>([]);

  /* -- UI state -- */
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [filters, setFilters] = useState<SidebarFilters>({
    source_type: null, category: null, status: null, monitor: null,
  });
  const [search, setSearch] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("keyword");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [offset, setOffset] = useState(0);
  const [showCategoryManager, setShowCategoryManager] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [showRules, setShowRules] = useState(false);
  const [newRule, setNewRule] = useState({
    pattern: "", match_on: "source_url", category: "", priority: 0,
  });

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  /* -- Data fetching (useCallback + useEffect pattern) -- */

  const fetchItems = useCallback(async () => {
    setLoading(true);
    const params: ListItemsParams = { limit: 50, offset };
    if (filters.source_type) params.source_type = filters.source_type;
    if (filters.category) params.category = filters.category;
    if (filters.status) params.status = filters.status;
    if (search.trim()) {
      const result = await searchItems(search.trim(), searchMode);
      setItems(result.results);
    } else {
      const result = await listItems(params);
      setItems(result.items);
    }
    setLoading(false);
  }, [filters, search, searchMode, offset]);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch("/api/agents", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setAgents(data.map((a: Record<string, unknown>) => ({
              name: String(a.name ?? "unknown"),
              color: String(a.color ?? "#3b82f6"),
            })));
          }
        }
      }
    } catch { /* ignore */ }
  }, []);

  const fetchSubscriptions = useCallback(async () => {
    setSubscriptions(await listSubscriptions());
  }, []);

  const fetchRules = useCallback(async () => {
    setRules(await listRules());
  }, []);

  // Extract unique categories from loaded items
  useEffect(() => {
    const cats = new Set<string>();
    items.forEach((item) => item.categories.forEach((c) => cats.add(c)));
    setCategories(Array.from(cats).sort());
  }, [items]);

  // Initial loads
  useEffect(() => { fetchItems(); }, [fetchItems]);
  useEffect(() => { fetchAgents(); fetchSubscriptions(); }, [fetchAgents, fetchSubscriptions]);

  /* -- Navigation -- */

  const openDetail = useCallback(async (id: string) => {
    setSelectedItemId(id);
    setView("detail");
    setDetailLoading(true);
    const [item, snaps] = await Promise.all([getItem(id), listSnapshots(id)]);
    setSelectedItem(item);
    setSnapshots(snaps);
    setDetailLoading(false);
  }, []);

  const backToList = useCallback(() => {
    setView("list");
    setSelectedItemId(null);
    setSelectedItem(null);
    setSnapshots([]);
    setShowAgentPicker(false);
  }, []);

  /* -- Actions -- */

  const handleDelete = useCallback(async (id: string) => {
    const ok = await deleteItem(id);
    if (ok) {
      setItems((prev) => prev.filter((i) => i.id !== id));
      if (selectedItemId === id) backToList();
    }
    setConfirmDelete(null);
  }, [selectedItemId, backToList]);

  const toggleFilter = <K extends keyof SidebarFilters>(
    key: K, value: SidebarFilters[K],
  ) => {
    setFilters((prev) => ({ ...prev, [key]: prev[key] === value ? null : value }));
    setOffset(0);
  };

  /* -- Sorting + monitor filtering (client-side) -- */

  const sortedItems = [...items].sort((a, b) => {
    if (sortMode === "newest") return b.created_at - a.created_at;
    if (sortMode === "updated") return b.updated_at - a.updated_at;
    return a.title.localeCompare(b.title);
  });

  const filteredItems = sortedItems.filter((item) => {
    if (!filters.monitor) return true;
    const ci = item.monitor.current_interval ?? 0;
    if (filters.monitor === "recent") return item.monitor.last_poll != null && ci > 0;
    if (filters.monitor === "active") return ci > 0 && ci < 2592000;
    if (filters.monitor === "slow") return ci >= 2592000;
    return true;
  });

  /* -- Subscription helpers -- */

  const getItemSubscribedAgents = useCallback(
    (item: KnowledgeItem): AgentSubscription[] =>
      subscriptions.filter((s) => item.categories.includes(s.category)),
    [subscriptions],
  );

  const addAgentToItem = useCallback(async (item: KnowledgeItem, agentName: string) => {
    for (const cat of item.categories) {
      await setSubscription({ agent_name: agentName, category: cat, auto_ingest: false });
    }
    await fetchSubscriptions();
  }, [fetchSubscriptions]);

  const removeAgentFromItem = useCallback(async (item: KnowledgeItem, agentName: string) => {
    for (const cat of item.categories) {
      await deleteSubscription(agentName, cat);
    }
    await fetchSubscriptions();
  }, [fetchSubscriptions]);

  /* -- Rule helpers -- */

  const handleCreateRule = useCallback(async (
    pattern: string, match_on: string, category: string, priority: number,
  ) => {
    await createRule({ pattern, match_on, category, priority });
    await fetchRules();
  }, [fetchRules]);

  const handleDeleteRule = useCallback(async (id: number) => {
    await deleteRule(id);
    await fetchRules();
  }, [fetchRules]);

  /* -- Time formatting -- */

  const timeAgo = (ts: number): string => {
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(ts * 1000).toLocaleDateString();
  };

  // ... rest of the component renders the sidebar, list view, detail view,
  // and category manager dialog as described in the spec.
  // See full code in Step 1 below.
}
```

The complete render sections include:

**Sidebar (`<nav>`):** Four collapsible sections — Sources (static list of `SOURCE_TYPES` as `Button` toggles), Categories (dynamic from `categories` state, each a `Button` toggle, plus "+ Manage" link), Status (three `Button` toggles), Monitoring (three `Button` toggles for recent/active/slow). Each button uses `aria-pressed` and `variant={active ? "secondary" : "ghost"}`.

**List view (`<main>`):** Search bar with `<Input>` and Keyword/Semantic radio toggle. Sort controls (Newest/Updated/A-Z). Item count badge. Item cards using `<Card>` with title, author line, summary (2-line clamp), category pills, "Shared with" line. Empty state with `<FolderOpen>` icon. Loading state.

**Detail view (`<main>`):** Back button. Title + author + date. Category/status/monitoring pills. "Shared with: agent1, agent2 + add agent" with inline dropdown populated from `/api/agents`. Summary box. `<Tabs>` with Content (full text, `whitespace-pre-wrap`), History (snapshot timeline with diff indicators and metadata deltas), Metadata (key-value table including monitor config). Action bar: Open source, Re-ingest, Download media (or "View local copy" if `media_path` exists), Delete with inline confirmation.

**Category manager (modal overlay):** Category list with counts and subscribed agents. Rules editor (expandable): rule table with pattern/match_on/category/priority, add form with `<Input>` fields and `<select>` for match_on, delete buttons.

**Root render:** Uses MemoryApp's `isMobile` swap pattern. Desktop: sidebar + (list or detail). Mobile: navigates between sidebar → list → detail with back buttons.

- [ ] **Step 2: Register the app in app-registry.ts**

Add after the `images` entry (line 32, launchpadOrder 13) in `desktop/src/registry/app-registry.ts`:

```ts
  { id: "library", name: "Library", icon: "book-open", category: "platform", component: () => import("@/apps/LibraryApp").then((m) => ({ default: m.LibraryApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: true, launchpadOrder: 13.5 },
```

- [ ] **Step 3: Verify TypeScript build**

Run: `cd /home/jay/tinyagentos/desktop && npx tsc --noEmit`
Expected: no type errors

- [ ] **Step 4: Run all frontend tests**

Run: `cd /home/jay/tinyagentos/desktop && npm test`
Expected: all tests pass (existing + knowledge.test.ts)

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add desktop/src/apps/LibraryApp.tsx desktop/src/registry/app-registry.ts
git commit -m "feat(library): add Library app with full UI"
```

---

## Task 3: Backend — Monitoring Decay Floor

**Files:**
- Modify: `tinyagentos/knowledge_monitor.py`
- Modify: `tests/test_knowledge_monitor.py`

- [ ] **Step 1: Write failing tests for 30-day floor**

Add to `tests/test_knowledge_monitor.py`:

```python
@pytest.mark.asyncio
async def test_decay_floors_at_30_days(monitor_service):
    """Decay should never push interval beyond 30 days (2592000 seconds)."""
    item_monitor = {
        "frequency": 86400,
        "current_interval": 2000000,
        "decay_rate": 2.0,
        "last_poll": 0,
        "last_hash": "abc",
    }
    new_interval = monitor_service.compute_next_interval(item_monitor, changed=False)
    assert new_interval <= 2592000, f"Interval {new_interval} exceeds 30-day floor"


@pytest.mark.asyncio
async def test_decay_does_not_stop_automatically(monitor_service):
    """Items should never stop polling — interval stays at floor, never becomes 0."""
    item_monitor = {
        "frequency": 86400,
        "current_interval": 2592000,
        "decay_rate": 2.0,
        "stop_after_days": 14,
        "last_poll": 0,
        "last_hash": "abc",
    }
    new_interval = monitor_service.compute_next_interval(item_monitor, changed=False)
    assert new_interval > 0, "Interval should not be zero (stopped)"
    assert new_interval == 2592000, "Interval should stay at 30-day floor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_knowledge_monitor.py -k "floor or stop_automatically" -v`
Expected: FAIL — current behaviour stops items or exceeds 30 days

- [ ] **Step 3: Update MonitorService decay logic**

In `tinyagentos/knowledge_monitor.py`, find the decay logic (in `compute_next_interval` or `poll_item`). Make two changes:

1. After applying the decay multiplier to `current_interval`, clamp to the 30-day ceiling:
   ```python
   DECAY_FLOOR = 2592000  # 30 days in seconds
   new_interval = min(current_interval * decay_rate, DECAY_FLOOR)
   ```

2. Remove or bypass the `stop_after_days` automatic stop — the interval should never become `0` from decay alone. The only way to stop is an explicit user action (setting `current_interval = 0` via API).

Keep unchanged: pinned override (always return `frequency`), decay reset on changes (reset to `frequency`).

- [ ] **Step 4: Run monitor tests**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_knowledge_monitor.py -v`
Expected: all monitor tests PASS (existing + 2 new)

- [ ] **Step 5: Run full backend test suite**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/knowledge_monitor.py tests/test_knowledge_monitor.py
git commit -m "feat(monitor): floor decay at 30 days instead of stopping"
```

---

## Task 4: Visual Polish and Manual Testing

**Files:**
- Possibly modify: `desktop/src/apps/LibraryApp.tsx` (minor tweaks from testing)

- [ ] **Step 1: Start dev server**

Run: `cd /home/jay/tinyagentos/desktop && npm run dev`

- [ ] **Step 2: Test golden path in browser**

Verify:
1. Library app appears in launchpad and opens
2. Sidebar renders with Sources, Categories, Status, Monitoring sections
3. Items load and display correctly
4. Source/category/status filters toggle and re-fetch
5. Search works in keyword mode
6. Clicking an item opens detail view
7. Back button returns to list
8. Detail shows summary, Content/History/Metadata tabs
9. Action bar buttons present: Open source, Re-ingest, Download media, Delete
10. Delete confirmation flow works
11. Category manager opens and shows categories + rules editor

- [ ] **Step 3: Test mobile layout**

Resize to <640px. Verify sidebar → list → detail back-navigation chain works.

- [ ] **Step 4: Test accessibility**

Verify: all buttons have `aria-label` or visible text, search input has `aria-label`, filter buttons have `aria-pressed`, tabs are keyboard-navigable, agent picker is keyboard-accessible.

- [ ] **Step 5: Fix any issues, commit**

```bash
cd /home/jay/tinyagentos
git add desktop/src/apps/LibraryApp.tsx
git commit -m "fix(library): polish from manual testing"
```

---

## TDD Summary

| Task | Tests | What it delivers |
|------|-------|------------------|
| 1 | 16 vitest unit tests | Type-safe API layer (`lib/knowledge.ts`) |
| 2 | TypeScript build check + existing tests | Full app UI (`LibraryApp.tsx`) + registry |
| 3 | 2 pytest tests | Backend monitoring decay floor at 30 days |
| 4 | Manual browser testing | Production-ready polish |

**Acceptance criteria:** All vitest + pytest tests pass, app launches in the desktop shell, list → detail → back flow works, sidebar filters functional, search (keyword + semantic) works, category manager with rules editor works, agent sharing contextual, monitoring sidebar filters functional, mobile layout navigable, ARIA-compliant.
