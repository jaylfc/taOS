import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  BookOpen,
  Search,
  Trash2,
  ChevronLeft,
  FolderOpen,
  ExternalLink,
  RefreshCw,
  Download,
  Settings2,
  Activity,
  Clock,
  AlertCircle,
} from "lucide-react";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import {
  Button,
  Card,
  CardHeader,
  CardContent,
  Input,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui";
import {
  listItems,
  getItem,
  deleteItem,
  searchItems,
  listSnapshots,
  listRules,
  createRule,
  deleteRule,
  listSubscriptions,
  setSubscription,
  deleteSubscription,
  ingestUrl,
} from "@/lib/knowledge";
import type {
  KnowledgeItem,
  Snapshot,
  CategoryRule,
  AgentSubscription,
  ListItemsParams,
} from "@/lib/knowledge";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { useDragSource } from "@/shell/dnd/use-drag-source";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type SearchMode = "keyword" | "semantic";
type SortMode = "newest" | "updated" | "alpha";
type MonitorFilter = "recent" | "active" | "slow" | null;

interface SidebarFilters {
  source_type: string | null;
  category: string | null;
  status: string | null;
  monitor: MonitorFilter;
}

interface AgentInfo {
  name: string;
  color: string;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const SOURCE_TYPES = [
  "reddit",
  "youtube",
  "github",
  "x",
  "article",
  "file",
  "manual",
] as const;

const STATUS_OPTIONS = ["ready", "processing", "error"] as const;

const SOURCE_LABELS: Record<string, string> = {
  reddit: "Reddit",
  youtube: "YouTube",
  github: "GitHub",
  x: "X",
  article: "Articles",
  file: "Files",
  manual: "Manual",
};

interface SourceStorage {
  source_type: string;
  bytes: number;
  item_count: number;
}

interface ItemStorageRow {
  id: string;
  title: string;
  source_type: string;
  bytes: number;
}

interface StorageViewData {
  total_bytes: number;
  cap_bytes: number;
  paused_at_cap: boolean;
  sources: SourceStorage[];
  items: ItemStorageRow[];
}

const STORAGE_CAP_BYTES = 50 * 1024 * 1024 * 1024;

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const fmtBytes = (b: number): string => {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const mockItemBytes = (id: string): number => {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i);
    hash |= 0;
  }
  return (Math.abs(hash) % 4000 + 100) * 1024 * 1024;
};

const deriveMockStorageData = (items: KnowledgeItem[]): StorageViewData => {
  const itemRows: ItemStorageRow[] = items.map((item) => ({
    id: item.id,
    title: item.title || "Untitled",
    source_type: item.source_type,
    bytes: mockItemBytes(item.id),
  }));
  const totalBytes = itemRows.reduce((sum, row) => sum + row.bytes, 0);
  const sourceMap = new Map<string, { bytes: number; count: number }>();
  for (const row of itemRows) {
    const existing = sourceMap.get(row.source_type) || { bytes: 0, count: 0 };
    sourceMap.set(row.source_type, { bytes: existing.bytes + row.bytes, count: existing.count + 1 });
  }
  const sources: SourceStorage[] = Array.from(sourceMap.entries())
    .map(([source_type, data]) => ({ source_type, bytes: data.bytes, item_count: data.count }))
    .sort((a, b) => b.bytes - a.bytes);
  return {
    total_bytes: totalBytes,
    cap_bytes: STORAGE_CAP_BYTES,
    paused_at_cap: totalBytes >= STORAGE_CAP_BYTES,
    sources,
    items: itemRows.sort((a, b) => b.bytes - a.bytes),
  };
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const timeAgo = (ts: number): string => {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
};

const statusColor = (status: string): string => {
  if (status === "ready") return "bg-green-500/15 text-green-400 border-green-500/30";
  if (status === "processing") return "bg-amber-500/15 text-amber-400 border-amber-500/30";
  if (status === "error") return "bg-red-500/15 text-red-400 border-red-500/30";
  return "bg-white/10 text-shell-text-tertiary border-white/10";
};

/* ------------------------------------------------------------------ */
/*  KnowledgeItemDragWrapper                                           */
/* ------------------------------------------------------------------ */

function KnowledgeItemDragWrapper({
  item,
  children,
}: {
  item: KnowledgeItem;
  children: React.ReactNode;
}) {
  const sourceUrl = item.source_url || undefined;
  const { dragHandlers } = useDragSource({
    payload: { kind: "knowledge", id: item.id, title: item.title, url: sourceUrl },
    htmlMirror: {
      "text/plain": item.title || item.id,
      ...(sourceUrl ? { "text/uri-list": sourceUrl } : {}),
    },
  });
  return <div {...dragHandlers}>{children}</div>;
}

/* ------------------------------------------------------------------ */
/*  LibraryApp                                                         */
/* ------------------------------------------------------------------ */

export function LibraryApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- view state ---------- */
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);

  /* ---------- list state ---------- */
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("keyword");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [offset] = useState(0);
  const [filters, setFilters] = useState<SidebarFilters>({
    source_type: null,
    category: null,
    status: null,
    monitor: null,
  });

  const [activeView, setActiveView] = useState<"items" | "storage">("items");

  /* ---------- detail state ---------- */
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  /* ---------- subscriptions ---------- */
  const [subscriptions, setSubscriptions] = useState<AgentSubscription[]>([]);

  /* ---------- agents ---------- */
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  /* ---------- category manager ---------- */
  const [categoryManagerOpen, setCategoryManagerOpen] = useState(false);
  const categoryManagerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(categoryManagerRef, categoryManagerOpen);
  const [rules, setRules] = useState<CategoryRule[]>([]);
  const [rulesExpanded, setRulesExpanded] = useState(false);
  const [newRule, setNewRule] = useState({
    pattern: "",
    match_on: "source_url",
    category: "",
    priority: 10,
  });

  /* ---------- mobile ---------- */
  const isMobile = useIsMobile();
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      if (search.trim()) {
        const result = await searchItems(search.trim(), searchMode);
        setItems(result.results);
      } else {
        const params: ListItemsParams = { limit: 50, offset };
        if (filters.source_type) params.source_type = filters.source_type;
        if (filters.category) params.category = filters.category;
        if (filters.status) params.status = filters.status;
        const result = await listItems(params);
        setItems(result.items);
      }
    } catch {
      setItems([]);
    }
    setLoading(false);
  }, [filters, search, searchMode, offset]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

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
            setAgents(
              data.map((a: Record<string, unknown>) => ({
                name: String(a.name ?? "unknown"),
                color: String(a.color ?? "#3b82f6"),
              })),
            );
          }
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const fetchSubscriptions = useCallback(async () => {
    const subs = await listSubscriptions();
    setSubscriptions(subs);
  }, []);

  useEffect(() => {
    fetchSubscriptions();
  }, [fetchSubscriptions]);

  const fetchRules = useCallback(async () => {
    const r = await listRules();
    setRules(r);
  }, []);

  const openDetail = useCallback(
    async (item: KnowledgeItem) => {
      setSelectedItem(item);
      setConfirmDelete(false);
      setAgentPickerOpen(false);

      // Load snapshots
      setSnapshotsLoading(true);
      try {
        const snaps = await listSnapshots(item.id);
        setSnapshots(snaps);
      } catch {
        setSnapshots([]);
      }
      setSnapshotsLoading(false);

      // Refresh full item from server
      setDetailLoading(true);
      try {
        const full = await getItem(item.id);
        if (full) setSelectedItem(full);
      } catch {
        /* ignore */
      }
      setDetailLoading(false);
    },
    [],
  );

  const goBackToList = useCallback(() => {
    setSelectedItem(null);
    setSnapshots([]);
    setConfirmDelete(false);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Subscription helpers                                             */
  /* ---------------------------------------------------------------- */

  const getItemSubscribedAgents = useCallback(
    (item: KnowledgeItem): string[] => {
      const cats = new Set(item.categories);
      const names = new Set<string>();
      for (const sub of subscriptions) {
        if (cats.has(sub.category)) names.add(sub.agent_name);
      }
      return Array.from(names);
    },
    [subscriptions],
  );

  const addAgentToItem = useCallback(
    async (item: KnowledgeItem, agentName: string) => {
      const cats = item.categories.length > 0 ? item.categories : ["default"];
      for (const cat of cats) {
        await setSubscription({ agent_name: agentName, category: cat, auto_ingest: true });
      }
      await fetchSubscriptions();
    },
    [fetchSubscriptions],
  );

  const removeAgentFromItem = useCallback(
    async (item: KnowledgeItem, agentName: string) => {
      const cats = item.categories.length > 0 ? item.categories : ["default"];
      for (const cat of cats) {
        await deleteSubscription(agentName, cat);
      }
      await fetchSubscriptions();
    },
    [fetchSubscriptions],
  );

  /* ---------------------------------------------------------------- */
  /*  Sorting / filtering                                              */
  /* ---------------------------------------------------------------- */

  const sortedItems = useMemo(() => {
    const copy = [...items];
    if (sortMode === "newest") copy.sort((a, b) => b.created_at - a.created_at);
    else if (sortMode === "updated") copy.sort((a, b) => b.updated_at - a.updated_at);
    else if (sortMode === "alpha") copy.sort((a, b) => a.title.localeCompare(b.title));
    return copy;
  }, [items, sortMode]);

  const filteredItems = useMemo(
    () =>
      sortedItems.filter((item) => {
        if (!filters.monitor) return true;
        const ci = item.monitor.current_interval ?? 0;
        if (filters.monitor === "recent")
          return item.monitor.last_poll != null && ci > 0;
        if (filters.monitor === "active") return ci > 0 && ci < 2592000;
        if (filters.monitor === "slow") return ci >= 2592000;
        return true;
      }),
    [sortedItems, filters.monitor],
  );

  /* derived categories from loaded items */
  const allCategories = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      for (const cat of item.categories) {
        counts[cat] = (counts[cat] ?? 0) + 1;
      }
    }
    return counts;
  }, [items]);

  /* ---------------------------------------------------------------- */
  /*  Actions                                                          */
  /* ---------------------------------------------------------------- */

  const handleDeleteItem = useCallback(async () => {
    if (!selectedItem) return;
    const ok = await deleteItem(selectedItem.id);
    if (ok) {
      setItems((prev) => prev.filter((i) => i.id !== selectedItem.id));
      goBackToList();
    }
  }, [selectedItem, goBackToList]);

  const handleReIngest = useCallback(async () => {
    if (!selectedItem?.source_url) return;
    await ingestUrl(selectedItem.source_url, {
      title: selectedItem.title,
      categories: selectedItem.categories,
    });
    // Refresh
    setDetailLoading(true);
    const full = await getItem(selectedItem.id);
    if (full) setSelectedItem(full);
    setDetailLoading(false);
  }, [selectedItem]);

  const handleAddRule = useCallback(async () => {
    if (!newRule.pattern || !newRule.category) return;
    const id = await createRule(newRule);
    if (id != null) {
      setRules((prev) => [...prev, { id, ...newRule }]);
      setNewRule({ pattern: "", match_on: "source_url", category: "", priority: 10 });
    }
  }, [newRule]);

  const handleDeleteRule = useCallback(async (id: number) => {
    await deleteRule(id);
    setRules((prev) => prev.filter((r) => r.id !== id));
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Render helpers                                                   */
  /* ---------------------------------------------------------------- */

  const toggleFilter = <K extends keyof SidebarFilters>(
    key: K,
    value: SidebarFilters[K],
  ) => {
    setFilters((prev) => ({
      ...prev,
      [key]: prev[key] === value ? null : value,
    }));
  };

  /* ---------------------------------------------------------------- */
  /*  Sidebar UI                                                       */
  /* ---------------------------------------------------------------- */

  const sidebarUI = (
    <nav
      className="w-52 shrink-0 border-r border-white/5 bg-shell-surface/30 flex flex-col overflow-hidden"
      aria-label="Library filters"
    >
      {/* Header — desktop only; on mobile MobileSplitView provides the title bar */}
      {!isMobile && (
        <div className="flex items-center gap-2 px-3 py-3 border-b border-white/5 shrink-0">
          <BookOpen size={15} className="text-accent" />
          <h1 className="text-sm font-semibold">Library</h1>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2 space-y-4">
        {/* --- Sources --- */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
            Sources
          </p>
          <div className="space-y-0.5">
            {SOURCE_TYPES.map((src) => {
              const active = filters.source_type === src;
              return (
                <Button
                  key={src}
                  variant={active ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={active}
                  onClick={() => toggleFilter("source_type", src)}
                  className="w-full justify-start text-xs h-7 px-2"
                >
                  {SOURCE_LABELS[src] ?? src}
                </Button>
              );
            })}
          </div>
        </section>

        {/* --- Categories --- */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
            Categories
          </p>
          <div className="space-y-0.5">
            {Object.entries(allCategories).map(([cat, count]) => {
              const active = filters.category === cat;
              return (
                <Button
                  key={cat}
                  variant={active ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={active}
                  onClick={() => toggleFilter("category", cat)}
                  className="w-full justify-between text-xs h-7 px-2"
                >
                  <span className="truncate">{cat}</span>
                  <span className="text-shell-text-tertiary tabular-nums ml-1">
                    {count}
                  </span>
                </Button>
              );
            })}
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start text-xs h-7 px-2 text-accent"
              onClick={() => {
                fetchRules();
                setCategoryManagerOpen(true);
              }}
              aria-label="Manage categories"
            >
              + Manage
            </Button>
          </div>
        </section>

        {/* --- Status --- */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
            Status
          </p>
          <div className="space-y-0.5">
            {STATUS_OPTIONS.map((s) => {
              const active = filters.status === s;
              return (
                <Button
                  key={s}
                  variant={active ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={active}
                  onClick={() => toggleFilter("status", s)}
                  className="w-full justify-start text-xs h-7 px-2 capitalize"
                >
                  {s}
                </Button>
              );
            })}
          </div>
        </section>

        {/* --- Monitoring --- */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
            Monitoring
          </p>
          <div className="space-y-0.5">
            {(
              [
                { id: "recent", label: "Recent changes", icon: Clock },
                { id: "active", label: "Active polls", icon: Activity },
                { id: "slow", label: "Slow items", icon: AlertCircle },
              ] as const
            ).map(({ id, label, icon: Icon }) => {
              const active = filters.monitor === id;
              return (
                <Button
                  key={id}
                  variant={active ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={active}
                  onClick={() => toggleFilter("monitor", id)}
                  className="w-full justify-start text-xs h-7 px-2 gap-1.5"
                >
                  <Icon size={11} />
                  {label}
                </Button>
              );
            })}
          </div>
        </section>
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  List View UI                                                     */
  /* ---------------------------------------------------------------- */

  const hasActiveFilter =
    filters.source_type != null ||
    filters.category != null ||
    filters.status != null ||
    filters.monitor != null;

  const listViewUI = (
    <main className="flex-1 flex flex-col overflow-hidden">
      {/* Search + controls */}
      <div className="flex flex-col gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none z-10"
            />
            <Input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search knowledge base..."
              className="pl-8 h-8"
              aria-label="Search knowledge base"
            />
          </div>
          {isMobile && (
            <Button
              variant={hasActiveFilter ? "secondary" : "outline"}
              size="sm"
              aria-pressed={mobileFiltersOpen}
              onClick={() => setMobileFiltersOpen((v) => !v)}
              className="text-xs shrink-0"
              aria-label="Toggle filters"
            >
              Filters{hasActiveFilter ? " •" : ""}
            </Button>
          )}
          {!isMobile && (
            <div className="flex items-center gap-1 shrink-0" role="radiogroup" aria-label="Search mode">
              {(["keyword", "semantic"] as const).map((m) => (
                <Button
                  key={m}
                  variant={searchMode === m ? "secondary" : "outline"}
                  size="sm"
                  role="radio"
                  aria-checked={searchMode === m}
                  onClick={() => setSearchMode(m)}
                  className="capitalize text-xs"
                >
                  {m}
                </Button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-1 shrink-0" role="radiogroup" aria-label="View mode">
            {(["items", "storage"] as const).map((v) => (
              <Button
                key={v}
                variant={activeView === v ? "secondary" : "ghost"}
                size="sm"
                role="radio"
                aria-checked={activeView === v}
                onClick={() => setActiveView(v)}
                className="capitalize text-xs"
              >
                {v}
              </Button>
            ))}
          </div>
        </div>

        {/* Mobile: search mode + filters inline panel */}
        {isMobile && (
          <>
            <div className="flex items-center gap-1" role="radiogroup" aria-label="Search mode">
              {(["keyword", "semantic"] as const).map((m) => (
                <Button
                  key={m}
                  variant={searchMode === m ? "secondary" : "outline"}
                  size="sm"
                  role="radio"
                  aria-checked={searchMode === m}
                  onClick={() => setSearchMode(m)}
                  className="capitalize text-xs"
                >
                  {m}
                </Button>
              ))}
            </div>
            {mobileFiltersOpen && (
              <div className="border-t border-white/5 pt-2 space-y-3">
                {/* Source filter chips */}
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-1.5">Sources</p>
                  <div className="flex flex-wrap gap-1">
                    {SOURCE_TYPES.map((src) => {
                      const active = filters.source_type === src;
                      return (
                        <button
                          key={src}
                          type="button"
                          onClick={() => toggleFilter("source_type", src)}
                          aria-pressed={active}
                          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                            active
                              ? "bg-accent/20 border-accent/40 text-accent"
                              : "bg-white/5 border-white/10 text-shell-text-secondary"
                          }`}
                        >
                          {SOURCE_LABELS[src] ?? src}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {/* Category filter chips */}
                {Object.keys(allCategories).length > 0 && (
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-1.5">Categories</p>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(allCategories).map(([cat]) => {
                        const active = filters.category === cat;
                        return (
                          <button
                            key={cat}
                            type="button"
                            onClick={() => toggleFilter("category", cat)}
                            aria-pressed={active}
                            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                              active
                                ? "bg-accent/20 border-accent/40 text-accent"
                                : "bg-white/5 border-white/10 text-shell-text-secondary"
                            }`}
                          >
                            {cat}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {/* Status filter chips */}
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-1.5">Status</p>
                  <div className="flex flex-wrap gap-1">
                    {STATUS_OPTIONS.map((s) => {
                      const active = filters.status === s;
                      return (
                        <button
                          key={s}
                          type="button"
                          onClick={() => toggleFilter("status", s)}
                          aria-pressed={active}
                          className={`text-xs px-2.5 py-1 rounded-full border capitalize transition-colors ${
                            active
                              ? "bg-accent/20 border-accent/40 text-accent"
                              : "bg-white/5 border-white/10 text-shell-text-secondary"
                          }`}
                        >
                          {s}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {hasActiveFilter && (
                  <button
                    type="button"
                    onClick={() => {
                      setFilters({ source_type: null, category: null, status: null, monitor: null });
                      setMobileFiltersOpen(false);
                    }}
                    className="text-xs text-shell-text-tertiary underline"
                  >
                    Clear all filters
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {/* Sort + count */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-shell-text-tertiary">
            {filteredItems.length} item{filteredItems.length !== 1 ? "s" : ""}
          </span>
          <div className="flex items-center gap-1 ml-auto" role="radiogroup" aria-label="Sort order">
            {(
              [
                { id: "newest", label: "Newest" },
                { id: "updated", label: "Updated" },
                { id: "alpha", label: "A–Z" },
              ] as const
            ).map(({ id, label }) => (
              <Button
                key={id}
                variant={sortMode === id ? "secondary" : "ghost"}
                size="sm"
                role="radio"
                aria-checked={sortMode === id}
                onClick={() => setSortMode(id)}
                className="text-xs h-6 px-2"
              >
                {label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Items */}
      <div className={isMobile ? "flex-1 overflow-y-auto" : "flex-1 overflow-y-auto p-3 space-y-2"}>
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
            Loading library...
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-shell-text-tertiary">
            <FolderOpen size={36} className="opacity-30" />
            <p className="text-sm">
              {search ? "No results for your search" : "No items in library"}
            </p>
          </div>
        ) : isMobile ? (
          /* iOS 26 grouped list on mobile */
          <div style={{ padding: "8px 0 16px" }}>
            <div
              style={{
                margin: "0 12px",
                borderRadius: 16,
                background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.08)",
                overflow: "hidden",
              }}
            >
              {filteredItems.map((item, idx, arr) => {
                const sharedWith = getItemSubscribedAgents(item);
                return (
                  <KnowledgeItemDragWrapper key={item.id} item={item}>
                    <button
                      type="button"
                      onClick={() => openDetail(item)}
                      aria-label={`Open ${item.title}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        width: "100%",
                        padding: "14px 16px",
                        background: "none",
                        border: "none",
                        borderBottom: idx === arr.length - 1 ? "none" : "1px solid rgba(255,255,255,0.06)",
                        cursor: "pointer",
                        color: "inherit",
                        textAlign: "left",
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 15, fontWeight: 600, color: "rgba(255,255,255,0.95)", marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {item.title || "Untitled"}
                        </div>
                        <div style={{ fontSize: 12, color: "rgba(255,255,255,0.45)", marginBottom: sharedWith.length > 0 ? 4 : 0 }}>
                          {[item.author, SOURCE_LABELS[item.source_type] ?? item.source_type, timeAgo(item.created_at)]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                        {sharedWith.length > 0 && (
                          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)" }}>
                            Shared: {sharedWith.join(", ")}
                          </div>
                        )}
                      </div>
                      <span
                        className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border mr-2 ${statusColor(item.status)}`}
                      >
                        {item.status}
                      </span>
                      <svg width="8" height="14" viewBox="0 0 8 14" fill="none" style={{ color: "rgba(255,255,255,0.3)", flexShrink: 0 }}>
                        <path d="M1 1L7 7L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  </KnowledgeItemDragWrapper>
                );
              })}
            </div>
          </div>
        ) : (
          /* Desktop card list */
          filteredItems.map((item) => {
            const sharedWith = getItemSubscribedAgents(item);
            return (
              <KnowledgeItemDragWrapper key={item.id} item={item}>
                <Card
                  className="cursor-pointer hover:border-white/15 transition-colors"
                  onClick={() => openDetail(item)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openDetail(item);
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open ${item.title}`}
                >
                  <CardHeader className="pb-1 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-medium leading-snug line-clamp-1">
                        {item.title || "Untitled"}
                      </h3>
                      <span
                        className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${statusColor(item.status)}`}
                      >
                        {item.status}
                      </span>
                    </div>
                    <p className="text-[11px] text-shell-text-tertiary">
                      {[item.author, SOURCE_LABELS[item.source_type] ?? item.source_type, timeAgo(item.created_at)]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </CardHeader>
                  <CardContent className="pt-0 px-3 pb-3 space-y-2">
                    {item.summary && (
                      <p className="text-xs text-shell-text-secondary line-clamp-2 leading-relaxed">
                        {item.summary}
                      </p>
                    )}
                    {item.categories.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {item.categories.map((cat) => (
                          <span
                            key={cat}
                            className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] border border-accent/20"
                          >
                            {cat}
                          </span>
                        ))}
                      </div>
                    )}
                    {sharedWith.length > 0 && (
                      <p className="text-[10px] text-shell-text-tertiary">
                        Shared with: {sharedWith.join(", ")}
                      </p>
                    )}
                  </CardContent>
                </Card>
              </KnowledgeItemDragWrapper>
            );
          })
        )}
      </div>
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Storage Accounting View                                          */
  /* ---------------------------------------------------------------- */

  const storageViewUI = items.length === 0 ? (
    <main className="flex-1 flex flex-col overflow-hidden">
      <div className="flex flex-col gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <span className="text-xs font-medium">Storage Accounting</span>
        <span className="text-[11px] text-shell-text-tertiary">(mock)</span>
      </div>
      <div className="flex-1 flex items-center justify-center text-shell-text-tertiary text-sm">
        No items to account for
      </div>
    </main>
  ) : (() => {
    const data = deriveMockStorageData(items);
    const totalPct = Math.min(100, (data.total_bytes / data.cap_bytes) * 100);
    return (
      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="flex flex-col gap-2 px-4 py-3 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Storage Accounting</span>
            <span className="text-[11px] text-shell-text-tertiary">(mock)</span>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-shell-text-secondary">
                {fmtBytes(data.total_bytes)} used of {fmtBytes(data.cap_bytes)} cap
              </span>
              <span className="tabular-nums text-shell-text-tertiary">{totalPct.toFixed(1)}%</span>
            </div>
            <div className="h-2 w-full rounded-full bg-white/5" role="progressbar" aria-valuenow={totalPct} aria-valuemin={0} aria-valuemax={100}>
              <div
                className={`h-full rounded-full transition-all ${totalPct >= 100 ? "bg-red-500" : totalPct >= 80 ? "bg-amber-500" : "bg-sky-500"}`}
                style={{ width: `${totalPct}%` }}
              />
            </div>
            {data.paused_at_cap && (
              <div className="flex items-center gap-1.5 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-2 py-1">
                <AlertCircle size={12} aria-hidden="true" />
                Paused at cap: new heavy-tier downloads are blocked.
              </div>
            )}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <section>
            <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-2">By source</p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-shell-text-tertiary border-b border-white/5">
                  <th className="text-left pb-1.5 font-normal" scope="col">Source</th>
                  <th className="text-left pb-1.5 font-normal" scope="col">Items</th>
                  <th className="text-right pb-1.5 font-normal" scope="col">Bytes</th>
                  <th className="pb-1.5 font-normal w-32" scope="col">Share</th>
                </tr>
              </thead>
              <tbody>
                {data.sources.map((src) => {
                  const pct = data.total_bytes > 0 ? (src.bytes / data.total_bytes) * 100 : 0;
                  return (
                    <tr key={src.source_type} className="border-t border-white/5">
                      <td className="py-1.5 pr-3 text-shell-text-secondary">{SOURCE_LABELS[src.source_type] ?? src.source_type}</td>
                      <td className="py-1.5 pr-3 text-shell-text-tertiary tabular-nums">{src.item_count}</td>
                      <td className="py-1.5 pr-3 text-shell-text-secondary text-right tabular-nums">{fmtBytes(src.bytes)}</td>
                      <td className="py-1.5">
                        <div className="h-1.5 w-full rounded-full bg-white/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                          <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          <section>
            <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-2">By item</p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-shell-text-tertiary border-b border-white/5">
                  <th className="text-left pb-1.5 font-normal" scope="col">Title</th>
                  <th className="text-left pb-1.5 font-normal" scope="col">Source</th>
                  <th className="text-right pb-1.5 font-normal" scope="col">Bytes</th>
                  <th className="pb-1.5 font-normal w-32" scope="col">Share of cap</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => {
                  const pct = data.cap_bytes > 0 ? (item.bytes / data.cap_bytes) * 100 : 0;
                  return (
                    <tr key={item.id} className="border-t border-white/5">
                      <td className="py-1.5 pr-3 text-shell-text-secondary truncate max-w-[200px]">{item.title}</td>
                      <td className="py-1.5 pr-3 text-shell-text-tertiary">{SOURCE_LABELS[item.source_type] ?? item.source_type}</td>
                      <td className="py-1.5 pr-3 text-shell-text-secondary text-right tabular-nums">{fmtBytes(item.bytes)}</td>
                      <td className="py-1.5">
                        <div className="h-1.5 w-full rounded-full bg-white/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                          <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        </div>
      </main>
    );
  })();

  /* ---------------------------------------------------------------- */
  /*  Detail View UI                                                   */
  /* ---------------------------------------------------------------- */

  const detailViewUI = selectedItem ? (
    <main className="flex-1 flex flex-col overflow-hidden">
      {/* Back + header */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-5 pt-4 pb-3 border-b border-white/5">
          {/* Back button — desktop only; MobileSplitView nav bar handles mobile */}
          {!isMobile && (
            <Button
              variant="ghost"
              size="sm"
              onClick={goBackToList}
              className="text-xs mb-3 -ml-1 text-shell-text-secondary"
              aria-label="Back to library"
            >
              <ChevronLeft size={14} />
              Back to library
            </Button>
          )}

          <h2 className="text-lg font-semibold leading-snug mb-1">
            {detailLoading ? "Loading..." : selectedItem.title || "Untitled"}
          </h2>
          <p className="text-xs text-shell-text-tertiary mb-2">
            {[
              selectedItem.author,
              SOURCE_LABELS[selectedItem.source_type] ?? selectedItem.source_type,
              timeAgo(selectedItem.created_at),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>

          {/* Pills row */}
          <div className="flex flex-wrap items-center gap-1.5 mb-3">
            {selectedItem.categories.map((cat) => (
              <span
                key={cat}
                className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] border border-accent/20"
              >
                {cat}
              </span>
            ))}
            <span
              className={`px-2 py-0.5 rounded-full text-[11px] border ${statusColor(selectedItem.status)}`}
            >
              {selectedItem.status}
            </span>
            {(selectedItem.monitor.current_interval ?? 0) > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 text-[11px] border border-blue-500/20">
                monitoring
              </span>
            )}
          </div>

          {/* Shared with */}
          {(() => {
            const shared = getItemSubscribedAgents(selectedItem);
            const notShared = agents.filter((a) => !shared.includes(a.name));
            return (
              <div className="flex items-center flex-wrap gap-1 text-xs text-shell-text-secondary relative">
                <span>Shared with:</span>
                {shared.length === 0 ? (
                  <span className="text-shell-text-tertiary italic">no agents</span>
                ) : (
                  shared.map((name) => {
                    const ag = agents.find((a) => a.name === name);
                    return (
                      <button
                        key={name}
                        onClick={() => removeAgentFromItem(selectedItem, name)}
                        className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/5 border border-white/10 hover:border-red-500/40 hover:bg-red-500/10 transition-colors text-[11px]"
                        title={`Remove ${name}`}
                        aria-label={`Remove ${name} from shared agents`}
                      >
                        {ag && (
                          <span
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: ag.color }}
                            aria-hidden="true"
                          />
                        )}
                        {name}
                      </button>
                    );
                  })
                )}
                <div className="relative">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-[11px] text-accent"
                    onClick={() => setAgentPickerOpen((prev) => !prev)}
                    aria-label="Add agent"
                    aria-expanded={agentPickerOpen}
                    aria-haspopup="listbox"
                  >
                    + add agent
                  </Button>
                  {agentPickerOpen && notShared.length > 0 && (
                    <div
                      className="absolute top-full left-0 mt-1 z-50 bg-shell-surface border border-white/10 rounded-lg shadow-lg py-1 min-w-[140px]"
                      role="listbox"
                      aria-label="Select agent to add"
                    >
                      {notShared.map((ag) => (
                        <button
                          key={ag.name}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-white/5 text-left"
                          role="option"
                          aria-selected={false}
                          onClick={() => {
                            addAgentToItem(selectedItem, ag.name);
                            setAgentPickerOpen(false);
                          }}
                        >
                          <span
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ backgroundColor: ag.color }}
                            aria-hidden="true"
                          />
                          {ag.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })()}
        </div>

        {/* Summary box */}
        {selectedItem.summary && (
          <div className="px-5 py-3 border-b border-white/5">
            <Card className="bg-white/[0.02]">
              <CardContent className="px-4 py-3">
                <p className="text-xs text-shell-text-secondary leading-relaxed">
                  {selectedItem.summary}
                </p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Tabs */}
        <div className="px-5 py-3 flex-1">
          <Tabs defaultValue="content">
            <TabsList>
              <TabsTrigger value="content">Content</TabsTrigger>
              <TabsTrigger value="history">
                History{snapshots.length > 0 ? ` (${snapshots.length})` : ""}
              </TabsTrigger>
              <TabsTrigger value="metadata">Metadata</TabsTrigger>
            </TabsList>

            {/* Content tab */}
            <TabsContent value="content">
              <div className="max-h-[320px] overflow-y-auto rounded-lg bg-white/[0.02] border border-white/5 p-3">
                {selectedItem.content ? (
                  <pre className="text-xs text-shell-text-secondary whitespace-pre-wrap leading-relaxed font-sans">
                    {selectedItem.content}
                  </pre>
                ) : (
                  <p className="text-xs text-shell-text-tertiary italic">
                    No content available
                  </p>
                )}
              </div>
            </TabsContent>

            {/* History tab */}
            <TabsContent value="history">
              <div className="space-y-2 max-h-[320px] overflow-y-auto">
                {snapshotsLoading ? (
                  <p className="text-xs text-shell-text-tertiary py-4 text-center">
                    Loading history...
                  </p>
                ) : snapshots.length === 0 ? (
                  <p className="text-xs text-shell-text-tertiary py-4 text-center italic">
                    No snapshots recorded yet
                  </p>
                ) : (
                  snapshots.map((snap) => (
                    <Card key={snap.id} className="bg-white/[0.02]">
                      <CardContent className="px-3 py-2.5 space-y-1.5">
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-medium text-shell-text-secondary">
                            {new Date(snap.snapshot_at * 1000).toLocaleString()}
                          </span>
                          <span className="text-[10px] text-shell-text-tertiary font-mono">
                            #{snap.content_hash.slice(0, 7)}
                          </span>
                        </div>
                        {snap.diff_json && Object.keys(snap.diff_json).length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {Object.entries(snap.diff_json).map(([k, v]) => (
                              <span
                                key={k}
                                className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 text-[10px] border border-amber-500/20"
                              >
                                {k}: {String(v)}
                              </span>
                            ))}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  ))
                )}
              </div>
            </TabsContent>

            {/* Metadata tab */}
            <TabsContent value="metadata">
              <div className="max-h-[320px] overflow-y-auto space-y-3">
                {/* Raw metadata */}
                {Object.keys(selectedItem.metadata).length > 0 && (
                  <table className="w-full text-xs">
                    <tbody>
                      {Object.entries(selectedItem.metadata).map(([k, v]) => (
                        <tr key={k} className="border-b border-white/5">
                          <td className="py-1.5 pr-3 text-shell-text-tertiary font-medium w-40 align-top">
                            {k}
                          </td>
                          <td className="py-1.5 text-shell-text-secondary break-all">
                            {typeof v === "object" ? JSON.stringify(v) : String(v)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {/* Monitor config */}
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-2">
                    Monitor Config
                  </p>
                  <table className="w-full text-xs">
                    <tbody>
                      {(
                        [
                          ["Current interval", selectedItem.monitor.current_interval != null ? `${selectedItem.monitor.current_interval}s` : "—"],
                          ["Frequency", selectedItem.monitor.frequency != null ? `${selectedItem.monitor.frequency}s` : "—"],
                          ["Decay rate", selectedItem.monitor.decay_rate ?? "—"],
                          ["Pinned", selectedItem.monitor.pinned ? "Yes" : "No"],
                          ["Last polled", selectedItem.monitor.last_poll != null ? timeAgo(selectedItem.monitor.last_poll) : "Never"],
                        ] as const
                      ).map(([label, val]) => (
                        <tr key={label} className="border-b border-white/5">
                          <td className="py-1.5 pr-3 text-shell-text-tertiary w-40">{label}</td>
                          <td className="py-1.5 text-shell-text-secondary">{val}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </div>

      {/* Action bar */}
      <div className="border-t border-white/5 px-5 py-2.5 flex items-center gap-2 shrink-0">
        {selectedItem.source_url && (
          <Button
            variant="outline"
            size="sm"
            className="text-xs gap-1.5"
            onClick={() => window.open(selectedItem.source_url, "_blank")}
            aria-label="Open source URL"
          >
            <ExternalLink size={12} />
            Open source
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="text-xs gap-1.5"
          onClick={handleReIngest}
          aria-label="Re-ingest this item"
        >
          <RefreshCw size={12} />
          Re-ingest
        </Button>
        {selectedItem.media_path && (
          <Button
            variant="outline"
            size="sm"
            className="text-xs gap-1.5"
            onClick={() => {
              if (selectedItem.media_path) {
                const a = document.createElement("a");
                a.href = selectedItem.media_path;
                a.download = selectedItem.title.replace(/\s+/g, "-");
                a.click();
              }
            }}
            aria-label="Download media"
          >
            <Download size={12} />
            Download media
          </Button>
        )}
        <div className="ml-auto">
          {confirmDelete ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-red-400">Confirm delete?</span>
              <Button
                variant="outline"
                size="sm"
                className="text-xs border-red-500/40 text-red-400 hover:bg-red-500/15"
                onClick={handleDeleteItem}
                aria-label="Confirm delete item"
              >
                Yes, delete
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => setConfirmDelete(false)}
                aria-label="Cancel delete"
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="text-xs gap-1.5 hover:text-red-400 hover:bg-red-500/15"
              onClick={() => setConfirmDelete(true)}
              aria-label="Delete this item"
            >
              <Trash2 size={12} />
              Delete
            </Button>
          )}
        </div>
      </div>
    </main>
  ) : null;

  /* ---------------------------------------------------------------- */
  /*  Category Manager Modal                                           */
  /* ---------------------------------------------------------------- */

  const categoryManagerUI = categoryManagerOpen ? (
    <div
      className={
        isMobile
          ? "fixed inset-0 z-50 flex items-end bg-black/50 backdrop-blur-sm"
          : "fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      }
      role="dialog"
      aria-modal="true"
      aria-label="Category manager"
      onClick={(e) => {
        if (e.target === e.currentTarget) setCategoryManagerOpen(false);
      }}
    >
      <div
        ref={categoryManagerRef}
        className="bg-shell-surface border border-white/10 shadow-2xl flex flex-col"
        style={
          isMobile
            ? { borderRadius: "20px 20px 0 0", width: "100%", maxHeight: "92%", overflowY: "auto" }
            : { borderRadius: 12, width: 560, maxWidth: "90vw", maxHeight: "80vh" }
        }
      >
        {/* Dialog header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5">
          <div className="flex items-center gap-2">
            <Settings2 size={15} className="text-accent" />
            <h2 className="text-sm font-semibold">Category Manager</h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCategoryManagerOpen(false)}
            aria-label="Close category manager"
            className="text-xs"
          >
            Close
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Categories list */}
          <section>
            <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary mb-2">
              Categories
            </p>
            {Object.keys(allCategories).length === 0 ? (
              <p className="text-xs text-shell-text-tertiary italic">
                No categories yet
              </p>
            ) : (
              <div className="space-y-1">
                {Object.entries(allCategories).map(([cat, count]) => {
                  const subscribedAgents = subscriptions
                    .filter((s) => s.category === cat)
                    .map((s) => s.agent_name);
                  return (
                    <div
                      key={cat}
                      className="flex items-center justify-between px-3 py-2 rounded-lg bg-white/[0.03] border border-white/5"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium">{cat}</span>
                        <span className="text-[10px] text-shell-text-tertiary">
                          {count} item{count !== 1 ? "s" : ""}
                        </span>
                        {subscribedAgents.length > 0 && (
                          <span className="text-[10px] text-shell-text-tertiary">
                            · {subscribedAgents.join(", ")}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          {/* Rules section (expandable) */}
          <section>
            <button
              className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-shell-text-tertiary hover:text-shell-text transition-colors w-full text-left"
              onClick={() => setRulesExpanded((prev) => !prev)}
              aria-expanded={rulesExpanded}
            >
              <span>{rulesExpanded ? "▾" : "▸"}</span>
              Advanced: Rules
            </button>

            {rulesExpanded && (
              <div className="mt-3 space-y-3">
                {/* Existing rules */}
                {rules.length > 0 ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-shell-text-tertiary">
                        <th className="text-left pb-1.5 font-normal">Pattern</th>
                        <th className="text-left pb-1.5 font-normal">Match on</th>
                        <th className="text-left pb-1.5 font-normal">Category</th>
                        <th className="text-left pb-1.5 font-normal w-8">Pri</th>
                        <th className="pb-1.5 w-8"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {rules.map((rule) => (
                        <tr key={rule.id} className="border-t border-white/5">
                          <td className="py-1.5 pr-2 font-mono text-[11px]">{rule.pattern}</td>
                          <td className="py-1.5 pr-2 text-shell-text-secondary">{rule.match_on}</td>
                          <td className="py-1.5 pr-2 text-shell-text-secondary">{rule.category}</td>
                          <td className="py-1.5 pr-2 text-shell-text-tertiary">{rule.priority}</td>
                          <td className="py-1.5">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-6 w-6 hover:text-red-400 hover:bg-red-500/15"
                              onClick={() => handleDeleteRule(rule.id)}
                              aria-label={`Delete rule for ${rule.pattern}`}
                            >
                              <Trash2 size={11} />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-xs text-shell-text-tertiary italic">No rules yet</p>
                )}

                {/* Add rule form */}
                <div className="border-t border-white/5 pt-3 space-y-2">
                  <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wider">
                    Add rule
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <label className="text-[10px] text-shell-text-tertiary" htmlFor="rule-pattern">
                        Pattern (glob)
                      </label>
                      <Input
                        id="rule-pattern"
                        value={newRule.pattern}
                        onChange={(e) => setNewRule((prev) => ({ ...prev, pattern: e.target.value }))}
                        placeholder="*.reddit.com/*"
                        className="h-7 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-[10px] text-shell-text-tertiary" htmlFor="rule-match-on">
                        Match on
                      </label>
                      <select
                        id="rule-match-on"
                        value={newRule.match_on}
                        onChange={(e) => setNewRule((prev) => ({ ...prev, match_on: e.target.value }))}
                        className="flex h-7 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-2 text-xs text-shell-text focus-visible:outline-none focus-visible:border-accent/40"
                      >
                        {["source_url", "source_type", "author", "title"].map((f) => (
                          <option key={f} value={f}>
                            {f}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className="text-[10px] text-shell-text-tertiary" htmlFor="rule-category">
                        Category
                      </label>
                      <Input
                        id="rule-category"
                        value={newRule.category}
                        onChange={(e) => setNewRule((prev) => ({ ...prev, category: e.target.value }))}
                        placeholder="AI/ML"
                        className="h-7 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-[10px] text-shell-text-tertiary" htmlFor="rule-priority">
                        Priority
                      </label>
                      <Input
                        id="rule-priority"
                        type="number"
                        value={newRule.priority}
                        onChange={(e) =>
                          setNewRule((prev) => ({ ...prev, priority: parseInt(e.target.value) || 10 }))
                        }
                        className="h-7 text-xs"
                      />
                    </div>
                  </div>
                  <Button
                    size="sm"
                    className="text-xs"
                    onClick={handleAddRule}
                    disabled={!newRule.pattern || !newRule.category}
                    aria-label="Add rule"
                  >
                    Add rule
                  </Button>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  ) : null;

  /* ---------------------------------------------------------------- */
  /*  Root render                                                      */
  /* ---------------------------------------------------------------- */

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none relative">
      <MobileSplitView
        selectedId={selectedItem?.id ?? null}
        onBack={goBackToList}
        listTitle="Library"
        detailTitle={selectedItem ? (selectedItem.title || "Untitled") : undefined}
        listWidth={700}
        list={
          activeView === "items" ? (
            /* List pane: sidebar filters + item list side by side on desktop,
               stacked (filters embedded above list) on mobile */
            <div className="flex h-full min-h-0 overflow-hidden">
              {/* Sidebar — always visible on desktop; hidden on mobile */}
              {!isMobile && sidebarUI}
              {listViewUI}
            </div>
          ) : (
            storageViewUI
          )
        }
        detail={
          activeView === "items"
            ? detailViewUI ?? (
                !isMobile ? (
                  <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
                    {loading ? "Loading..." : items.length === 0 ? "Add items to get started" : "Select an item"}
                  </div>
                ) : null
              )
            : null
        }
      />
      {categoryManagerUI}
    </div>
  );
}

