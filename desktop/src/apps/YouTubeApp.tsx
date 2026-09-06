import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  PlayCircle,
  Search,
  Trash2,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Download,
  Eye,
  FolderOpen,
  Clock,
  Check,
  Loader2,
  HardDrive,
  MonitorOff,
} from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  Input,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui";
import { listItems, getItem, deleteItem, ingestUrl } from "@/lib/knowledge";
import type { KnowledgeItem } from "@/lib/knowledge";
import {
  ingestVideo,
  downloadVideo,
  getDownloadStatus,
  getTranscript,
  formatTimestamp,
} from "@/lib/youtube";
import type { TranscriptSegment, Chapter, YouTubeMetadata, DownloadStatus } from "@/lib/youtube";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type View = "list" | "detail";
type SortMode = "newest" | "updated" | "alpha";
type SidebarFilter = "all" | "downloaded" | "monitored" | { channel: string } | { category: string } | { status: string };

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

const formatViews = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
};

const formatDuration = (seconds: number): string => formatTimestamp(seconds);

function getYouTubeMeta(item: KnowledgeItem): YouTubeMetadata | null {
  const m = item.metadata;
  if (!m || typeof m !== "object") return null;
  if (!("video_id" in m)) return null;
  return m as unknown as YouTubeMetadata;
}

const DOWNLOAD_QUALITY_OPTIONS = ["360p", "720p", "1080p", "Best"] as const;
type DownloadQuality = (typeof DOWNLOAD_QUALITY_OPTIONS)[number];

/* ------------------------------------------------------------------ */
/*  YouTubeApp                                                         */
/* ------------------------------------------------------------------ */

export function YouTubeApp({ windowId: _windowId }: { windowId: string }) {
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  /* ---------- view state ---------- */
  const [view, setView] = useState<View>("list");
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null);

  /* ---------- list state ---------- */
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [sidebarFilter, setSidebarFilter] = useState<SidebarFilter>("all");

  /* ---------- detail state ---------- */
  const [detailLoading, setDetailLoading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [transcriptSegments, setTranscriptSegments] = useState<TranscriptSegment[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [transcriptSearch, setTranscriptSearch] = useState("");
  const [expandedChapters, setExpandedChapters] = useState<Set<number>>(new Set());

  /* ---------- download state ---------- */
  const [downloadStatus, setDownloadStatus] = useState<DownloadStatus>({ status: "idle" });
  const [qualityMenuOpen, setQualityMenuOpen] = useState(false);
  const downloadPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ---------- player ---------- */
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listItems({ source_type: "youtube", limit: 100 });
      setItems(result.items);
    } catch {
      setItems([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  /* ---------- derived channels + categories ---------- */
  const allChannels = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      const meta = getYouTubeMeta(item);
      if (meta?.channel) {
        counts[meta.channel] = (counts[meta.channel] ?? 0) + 1;
      }
    }
    return counts;
  }, [items]);

  const allCategories = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      for (const cat of item.categories) {
        counts[cat] = (counts[cat] ?? 0) + 1;
      }
    }
    return counts;
  }, [items]);

  /* ---------- filtered / sorted items ---------- */
  const filteredItems = useMemo(() => {
    let list = [...items];

    // Sidebar filter
    if (sidebarFilter === "downloaded") {
      list = list.filter((i) => i.media_path != null);
    } else if (sidebarFilter === "monitored") {
      list = list.filter((i) => (i.monitor.current_interval ?? 0) > 0);
    } else if (typeof sidebarFilter === "object" && "channel" in sidebarFilter) {
      list = list.filter((i) => getYouTubeMeta(i)?.channel === sidebarFilter.channel);
    } else if (typeof sidebarFilter === "object" && "category" in sidebarFilter) {
      list = list.filter((i) => i.categories.includes(sidebarFilter.category));
    } else if (typeof sidebarFilter === "object" && "status" in sidebarFilter) {
      list = list.filter((i) => i.status === sidebarFilter.status);
    }

    // Text search
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          (getYouTubeMeta(i)?.channel ?? "").toLowerCase().includes(q) ||
          i.categories.some((c) => c.toLowerCase().includes(q)),
      );
    }

    // Sort
    if (sortMode === "newest") list.sort((a, b) => b.created_at - a.created_at);
    else if (sortMode === "updated") list.sort((a, b) => b.updated_at - a.updated_at);
    else if (sortMode === "alpha") list.sort((a, b) => a.title.localeCompare(b.title));

    return list;
  }, [items, sidebarFilter, search, sortMode]);

  /* ---------------------------------------------------------------- */
  /*  Detail open / close                                              */
  /* ---------------------------------------------------------------- */

  const openDetail = useCallback(async (item: KnowledgeItem) => {
    setSelectedItem(item);
    setView("detail");
    setConfirmDelete(false);
    setTranscriptOpen(false);
    setTranscriptSearch("");
    setExpandedChapters(new Set());
    setDownloadStatus({ status: "idle" });
    setQualityMenuOpen(false);

    // Fetch full item
    setDetailLoading(true);
    try {
      const full = await getItem(item.id);
      if (full) setSelectedItem(full);
    } catch {
      /* ignore */
    }
    setDetailLoading(false);

    // Fetch download status
    try {
      const ds = await getDownloadStatus(item.id);
      setDownloadStatus(ds);
    } catch {
      /* ignore */
    }
  }, []);

  const goBack = useCallback(() => {
    setView("list");
    setSelectedItem(null);
    setTranscriptSegments([]);
    setTranscriptOpen(false);
    setDownloadStatus({ status: "idle" });
    if (downloadPollRef.current) {
      clearInterval(downloadPollRef.current);
      downloadPollRef.current = null;
    }
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Transcript                                                       */
  /* ---------------------------------------------------------------- */

  const loadTranscript = useCallback(async (itemId: string) => {
    setTranscriptLoading(true);
    try {
      const segs = await getTranscript(itemId);
      setTranscriptSegments(segs);
    } catch {
      setTranscriptSegments([]);
    }
    setTranscriptLoading(false);
  }, []);

  const handleToggleTranscript = useCallback(() => {
    setTranscriptOpen((prev) => {
      const next = !prev;
      if (next && selectedItem && transcriptSegments.length === 0) {
        // Load on first open if we don't have them yet
        const meta = getYouTubeMeta(selectedItem);
        const segments = meta?.transcript_segments ?? [];
        if (segments.length > 0) {
          setTranscriptSegments(segments);
        } else {
          loadTranscript(selectedItem.id);
        }
      }
      return next;
    });
  }, [selectedItem, transcriptSegments, loadTranscript]);

  const seekTo = useCallback((seconds: number) => {
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.postMessage(
        JSON.stringify({ event: "command", func: "seekTo", args: [seconds, true] }),
        "*",
      );
    }
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Download                                                         */
  /* ---------------------------------------------------------------- */

  const startDownload = useCallback(
    async (quality: DownloadQuality) => {
      if (!selectedItem) return;
      setQualityMenuOpen(false);
      setDownloadStatus({ status: "downloading" });

      await downloadVideo(selectedItem.id, quality);

      // Poll for status
      if (downloadPollRef.current) clearInterval(downloadPollRef.current);
      downloadPollRef.current = setInterval(async () => {
        const ds = await getDownloadStatus(selectedItem.id);
        setDownloadStatus(ds);
        if (ds.status === "complete" || ds.status === "error") {
          if (downloadPollRef.current) {
            clearInterval(downloadPollRef.current);
            downloadPollRef.current = null;
          }
          // Refresh item to pick up media_path
          const full = await getItem(selectedItem.id);
          if (full) {
            setSelectedItem(full);
            setItems((prev) => prev.map((i) => (i.id === full.id ? full : i)));
          }
        }
      }, 2000);
    },
    [selectedItem],
  );

  /* Cleanup on unmount */
  useEffect(() => {
    return () => {
      if (downloadPollRef.current) clearInterval(downloadPollRef.current);
    };
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Actions                                                          */
  /* ---------------------------------------------------------------- */

  const handleReIngest = useCallback(async () => {
    if (!selectedItem?.source_url) return;
    setDetailLoading(true);
    await ingestVideo(selectedItem.source_url);
    await ingestUrl(selectedItem.source_url, {
      title: selectedItem.title,
      categories: selectedItem.categories,
    });
    const full = await getItem(selectedItem.id);
    if (full) setSelectedItem(full);
    setDetailLoading(false);
  }, [selectedItem]);

  const handleDelete = useCallback(async () => {
    if (!selectedItem) return;
    const ok = await deleteItem(selectedItem.id);
    if (ok) {
      setItems((prev) => prev.filter((i) => i.id !== selectedItem.id));
      goBack();
    }
  }, [selectedItem, goBack]);

  /* ---------------------------------------------------------------- */
  /*  Filtered transcript segments                                     */
  /* ---------------------------------------------------------------- */

  const filteredSegments = useMemo(() => {
    if (!transcriptSearch.trim()) return transcriptSegments;
    const q = transcriptSearch.toLowerCase();
    return transcriptSegments.filter((s) => s.text.toLowerCase().includes(q));
  }, [transcriptSegments, transcriptSearch]);

  /* Group segments by chapter */
  const segmentsByChapter = useMemo(() => {
    const meta = selectedItem ? getYouTubeMeta(selectedItem) : null;
    const chapters: Chapter[] = meta?.chapters ?? [];
    if (chapters.length === 0) return null;

    return chapters.map((chapter, idx) => ({
      chapter,
      idx,
      segments: filteredSegments.filter(
        (s) => s.start >= chapter.start_time && s.start < chapter.end_time,
      ),
    }));
  }, [selectedItem, filteredSegments]);

  /* ---------------------------------------------------------------- */
  /*  Download status badge                                            */
  /* ---------------------------------------------------------------- */

  const downloadBadge = (item: KnowledgeItem) => {
    if (item.media_path) {
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border bg-green-500/15 text-green-400 border-green-500/30">
          <HardDrive size={9} aria-hidden="true" />
          downloaded
        </span>
      );
    }
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded border bg-white/5 text-shell-text-tertiary border-white/10">
        not downloaded
      </span>
    );
  };

  /* ---------------------------------------------------------------- */
  /*  Sidebar                                                          */
  /* ---------------------------------------------------------------- */

  const sidebarActiveClass = (filter: SidebarFilter) => {
    if (JSON.stringify(sidebarFilter) === JSON.stringify(filter))
      return "bg-white/10 text-shell-text";
    return "";
  };

  const sidebarUI = (
    <nav
      className={
        isMobile
          ? "w-full flex flex-col overflow-hidden h-full"
          : "w-52 shrink-0 border-r border-white/5 bg-shell-surface/30 flex flex-col overflow-hidden"
      }
      aria-label="YouTube Library filters"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-3 border-b border-white/5 shrink-0">
        <PlayCircle size={15} className="text-red-500" aria-hidden="true" />
        <h1 className="text-sm font-semibold">YouTube</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-4">
        {/* All */}
        <div className="space-y-0.5">
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={sidebarFilter === "all"}
            onClick={() => setSidebarFilter("all")}
            className={`w-full justify-start text-xs h-7 px-2 ${sidebarActiveClass("all")}`}
          >
            All Videos
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={sidebarFilter === "downloaded"}
            onClick={() => setSidebarFilter("downloaded")}
            className={`w-full justify-start text-xs h-7 px-2 ${sidebarActiveClass("downloaded")}`}
          >
            <HardDrive size={11} aria-hidden="true" className="mr-1" />
            Downloaded
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={sidebarFilter === "monitored"}
            onClick={() => setSidebarFilter("monitored")}
            className={`w-full justify-start text-xs h-7 px-2 ${sidebarActiveClass("monitored")}`}
          >
            <Eye size={11} aria-hidden="true" className="mr-1" />
            Monitored
          </Button>
        </div>

        {/* Channels */}
        {Object.keys(allChannels).length > 0 && (
          <section>
            <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
              Channels
            </p>
            <div className="space-y-0.5">
              {Object.entries(allChannels).map(([channel, count]) => {
                const f: SidebarFilter = { channel };
                return (
                  <Button
                    key={channel}
                    variant="ghost"
                    size="sm"
                    aria-pressed={
                      typeof sidebarFilter === "object" &&
                      "channel" in sidebarFilter &&
                      sidebarFilter.channel === channel
                    }
                    onClick={() => setSidebarFilter(f)}
                    className={`w-full justify-between text-xs h-7 px-2 ${sidebarActiveClass(f)}`}
                  >
                    <span className="truncate">{channel}</span>
                    <span className="text-shell-text-tertiary tabular-nums ml-1 shrink-0">
                      {count}
                    </span>
                  </Button>
                );
              })}
            </div>
          </section>
        )}

        {/* Categories */}
        {Object.keys(allCategories).length > 0 && (
          <section>
            <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
              Categories
            </p>
            <div className="space-y-0.5">
              {Object.entries(allCategories).map(([cat, count]) => {
                const f: SidebarFilter = { category: cat };
                return (
                  <Button
                    key={cat}
                    variant="ghost"
                    size="sm"
                    aria-pressed={
                      typeof sidebarFilter === "object" &&
                      "category" in sidebarFilter &&
                      sidebarFilter.category === cat
                    }
                    onClick={() => setSidebarFilter(f)}
                    className={`w-full justify-between text-xs h-7 px-2 ${sidebarActiveClass(f)}`}
                  >
                    <span className="truncate">{cat}</span>
                    <span className="text-shell-text-tertiary tabular-nums ml-1 shrink-0">
                      {count}
                    </span>
                  </Button>
                );
              })}
            </div>
          </section>
        )}

        {/* Status */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">
            Status
          </p>
          <div className="space-y-0.5">
            {(["ready", "processing", "error"] as const).map((s) => {
              const f: SidebarFilter = { status: s };
              return (
                <Button
                  key={s}
                  variant="ghost"
                  size="sm"
                  aria-pressed={
                    typeof sidebarFilter === "object" &&
                    "status" in sidebarFilter &&
                    sidebarFilter.status === s
                  }
                  onClick={() => setSidebarFilter(f)}
                  className={`w-full justify-start text-xs h-7 px-2 capitalize ${sidebarActiveClass(f)}`}
                >
                  {s}
                </Button>
              );
            })}
          </div>
        </section>
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  List View                                                        */
  /* ---------------------------------------------------------------- */

  const listViewUI = (
    <main className="flex-1 flex flex-col overflow-hidden">
      {/* Search + sort */}
      <div className="flex flex-col gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none z-10"
            aria-hidden="true"
          />
          <Input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search videos..."
            className="pl-8 h-8"
            aria-label="Search YouTube videos"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-shell-text-tertiary">
            {filteredItems.length} video{filteredItems.length !== 1 ? "s" : ""}
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
      <div className="flex-1 overflow-y-auto p-3 space-y-2" role="list" aria-label="YouTube videos">
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
            <Loader2 size={16} className="animate-spin mr-2" aria-hidden="true" />
            Loading videos...
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-shell-text-tertiary">
            <FolderOpen size={36} className="opacity-30" aria-hidden="true" />
            <p className="text-sm">
              {search ? "No results for your search" : "No YouTube videos in library"}
            </p>
          </div>
        ) : (
          filteredItems.map((item) => {
            const meta = getYouTubeMeta(item);
            return (
              <Card
                key={item.id}
                className="cursor-pointer hover:border-white/15 transition-colors"
                onClick={() => openDetail(item)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openDetail(item);
                  }
                }}
                tabIndex={0}
                role="listitem button"
                aria-label={`Open ${item.title}`}
              >
                <CardContent className="p-3 flex gap-3">
                  {/* Thumbnail */}
                  <div className="shrink-0 w-24 h-14 rounded overflow-hidden bg-white/5 border border-white/10 flex items-center justify-center">
                    {item.thumbnail ? (
                      <img
                        src={item.thumbnail}
                        alt={`Thumbnail for ${item.title}`}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <PlayCircle
                        size={20}
                        className="text-shell-text-tertiary opacity-40"
                        aria-hidden="true"
                      />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0 space-y-1">
                    <h3 className="text-sm font-medium leading-snug line-clamp-2">
                      {item.title || "Untitled"}
                    </h3>
                    <div className="flex items-center gap-2 flex-wrap">
                      {meta?.channel && (
                        <span className="text-[11px] text-shell-text-tertiary truncate">
                          {meta.channel}
                        </span>
                      )}
                      {meta && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-shell-text-tertiary border border-white/10">
                          {formatViews(meta.views)} views · {formatDuration(meta.duration)}
                        </span>
                      )}
                      <span className="text-[10px] text-shell-text-tertiary ml-auto">
                        {timeAgo(item.created_at)}
                      </span>
                    </div>
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
                    <div className="flex items-center gap-1 flex-wrap">
                      {downloadBadge(item)}
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Transcript Panel                                                 */
  /* ---------------------------------------------------------------- */

  const transcriptPanelUI = selectedItem && (
    <div className="border-t border-white/5">
      {/* Toggle button */}
      <button
        className="w-full flex items-center justify-between px-5 py-2.5 text-xs font-medium hover:bg-white/[0.03] transition-colors"
        onClick={handleToggleTranscript}
        aria-expanded={transcriptOpen}
        aria-controls="transcript-panel"
      >
        <span className="flex items-center gap-1.5">
          <Clock size={12} aria-hidden="true" />
          Transcript
          {transcriptLoading && (
            <Loader2 size={11} className="animate-spin ml-1 text-shell-text-tertiary" aria-hidden="true" />
          )}
        </span>
        {transcriptOpen ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
      </button>

      {/* Panel body */}
      <div
        id="transcript-panel"
        role="region"
        aria-label="Transcript"
        hidden={!transcriptOpen}
        className={transcriptOpen ? "border-t border-white/5" : ""}
      >
        {transcriptOpen && (
          <div className="px-4 py-2 space-y-2">
            {/* Search */}
            <div className="relative">
              <Search
                size={12}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={transcriptSearch}
                onChange={(e) => setTranscriptSearch(e.target.value)}
                placeholder="Search transcript..."
                className="pl-7 h-7 text-xs"
                aria-label="Search transcript"
              />
            </div>

            {/* Segments */}
            <div className="max-h-64 overflow-y-auto space-y-0.5 pr-1">
              {transcriptLoading ? (
                <p className="text-xs text-shell-text-tertiary py-3 text-center">
                  Loading transcript...
                </p>
              ) : filteredSegments.length === 0 && !transcriptLoading ? (
                transcriptSegments.length === 0 ? (
                  <p className="text-xs text-shell-text-tertiary py-3 text-center italic">
                    No transcript available
                  </p>
                ) : (
                  <p className="text-xs text-shell-text-tertiary py-3 text-center italic">
                    No matching segments
                  </p>
                )
              ) : segmentsByChapter ? (
                /* Chapters view */
                segmentsByChapter.map(({ chapter, idx, segments }) => (
                  <div key={idx} className="mb-1">
                    <button
                      className="w-full flex items-center gap-1.5 text-[11px] font-medium text-shell-text-secondary hover:text-shell-text py-1 px-1 rounded hover:bg-white/5 transition-colors"
                      onClick={() =>
                        setExpandedChapters((prev) => {
                          const next = new Set(prev);
                          if (next.has(idx)) next.delete(idx);
                          else next.add(idx);
                          return next;
                        })
                      }
                      aria-expanded={expandedChapters.has(idx)}
                    >
                      {expandedChapters.has(idx) ? (
                        <ChevronDown size={11} aria-hidden="true" />
                      ) : (
                        <ChevronRight size={11} aria-hidden="true" />
                      )}
                      <span className="font-mono text-[10px] text-accent mr-1 shrink-0">
                        {formatTimestamp(chapter.start_time)}
                      </span>
                      <span className="truncate">{chapter.title}</span>
                    </button>
                    {expandedChapters.has(idx) && (
                      <div className="ml-3 space-y-0.5">
                        {segments.map((seg) => (
                          <button
                            key={seg.start}
                            className="w-full flex items-start gap-2 text-xs hover:bg-white/5 rounded px-1 py-0.5 text-left transition-colors"
                            onClick={() => seekTo(seg.start)}
                            aria-label={`Seek to ${formatTimestamp(seg.start)}: ${seg.text}`}
                          >
                            <span className="font-mono text-[10px] text-accent shrink-0 mt-0.5">
                              {formatTimestamp(seg.start)}
                            </span>
                            <span className="text-shell-text-secondary leading-snug">
                              {seg.text}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))
              ) : (
                /* Flat segments */
                filteredSegments.map((seg) => (
                  <button
                    key={seg.start}
                    className="w-full flex items-start gap-2 text-xs hover:bg-white/5 rounded px-1 py-0.5 text-left transition-colors"
                    onClick={() => seekTo(seg.start)}
                    aria-label={`Seek to ${formatTimestamp(seg.start)}: ${seg.text}`}
                  >
                    <span className="font-mono text-[10px] text-accent shrink-0 mt-0.5">
                      {formatTimestamp(seg.start)}
                    </span>
                    <span className="text-shell-text-secondary leading-snug">{seg.text}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Detail View                                                      */
  /* ---------------------------------------------------------------- */

  const detailViewUI = selectedItem ? (() => {
    const meta = getYouTubeMeta(selectedItem);
    const videoId = meta?.video_id ?? "";

    return (
      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto">
          {/* Back button */}
          <div className="px-5 pt-4 pb-2 border-b border-white/5 shrink-0">
            <Button
              variant="ghost"
              size="sm"
              onClick={goBack}
              className="text-xs mb-3 -ml-1 text-shell-text-secondary"
              aria-label="Back to YouTube library"
            >
              <ChevronLeft size={14} aria-hidden="true" />
              Back
            </Button>
            <h2 className="text-base font-semibold leading-snug mb-1">
              {detailLoading ? "Loading..." : selectedItem.title || "Untitled"}
            </h2>
            {meta?.channel && (
              <p className="text-xs text-shell-text-tertiary mb-2">{meta.channel}</p>
            )}
            {selectedItem.categories.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {selectedItem.categories.map((cat) => (
                  <span
                    key={cat}
                    className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] border border-accent/20"
                  >
                    {cat}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Player area */}
          <div className="px-5 py-3 border-b border-white/5">
            {selectedItem.media_path ? (
              /* Local downloaded video */
              <video
                src={`/api/knowledge/media/${selectedItem.media_path}`}
                controls
                className="w-full rounded-lg bg-black aspect-video"
                aria-label={`Video player for ${selectedItem.title}`}
              />
            ) : videoId ? (
              /* YouTube embed */
              <div className="relative aspect-video w-full rounded-lg overflow-hidden bg-black">
                <iframe
                  ref={iframeRef}
                  src={`https://www.youtube.com/embed/${videoId}?enablejsapi=1`}
                  title={selectedItem.title}
                  className="w-full h-full border-0"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                  aria-label={`YouTube player for ${selectedItem.title}`}
                />
              </div>
            ) : (
              <div className="flex items-center justify-center aspect-video w-full rounded-lg bg-white/5 border border-white/10 text-shell-text-tertiary text-sm">
                <PlayCircle size={32} className="opacity-30 mr-2" aria-hidden="true" />
                No video available
              </div>
            )}
          </div>

          {/* Summary card */}
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

          {/* Transcript collapsible panel */}
          {transcriptPanelUI}

          {/* Tabs */}
          <div className="px-5 py-3 flex-1">
            <Tabs defaultValue="transcript">
              <TabsList>
                <TabsTrigger value="transcript">Transcript</TabsTrigger>
                <TabsTrigger value="history">History</TabsTrigger>
                <TabsTrigger value="metadata">Metadata</TabsTrigger>
              </TabsList>

              {/* Transcript tab */}
              <TabsContent value="transcript">
                <div className="max-h-[280px] overflow-y-auto space-y-0.5 pr-1">
                  {transcriptSegments.length === 0 ? (
                    <div className="py-4 text-center">
                      <p className="text-xs text-shell-text-tertiary italic mb-2">
                        No transcript loaded
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs"
                        onClick={() => loadTranscript(selectedItem.id)}
                        aria-label="Load transcript"
                      >
                        Load transcript
                      </Button>
                    </div>
                  ) : (
                    transcriptSegments.map((seg) => (
                      <button
                        key={seg.start}
                        className="w-full flex items-start gap-2 text-xs hover:bg-white/5 rounded px-1 py-0.5 text-left transition-colors"
                        onClick={() => seekTo(seg.start)}
                        aria-label={`Seek to ${formatTimestamp(seg.start)}: ${seg.text}`}
                      >
                        <span className="font-mono text-[10px] text-accent shrink-0 mt-0.5">
                          {formatTimestamp(seg.start)}
                        </span>
                        <span className="text-shell-text-secondary leading-snug">{seg.text}</span>
                      </button>
                    ))
                  )}
                </div>
              </TabsContent>

              {/* History tab */}
              <TabsContent value="history">
                <p className="text-xs text-shell-text-tertiary py-4 text-center italic">
                  No history recorded yet
                </p>
              </TabsContent>

              {/* Metadata tab */}
              <TabsContent value="metadata">
                <div className="max-h-[280px] overflow-y-auto">
                  <table className="w-full text-xs">
                    <tbody>
                      {(
                        [
                          ["Video ID", meta?.video_id ?? "—"],
                          ["Channel", meta?.channel ?? selectedItem.author ?? "—"],
                          ["Views", meta?.views != null ? formatViews(meta.views) : "—"],
                          ["Likes", meta?.likes != null ? String(meta.likes) : "—"],
                          ["Duration", meta?.duration != null ? formatDuration(meta.duration) : "—"],
                          ["Upload date", meta?.upload_date ?? "—"],
                          ["Source URL", selectedItem.source_url],
                          ["Status", selectedItem.status],
                          ["Added", timeAgo(selectedItem.created_at)],
                        ] as const
                      ).map(([label, val]) => (
                        <tr key={label} className="border-b border-white/5">
                          <td className="py-1.5 pr-3 text-shell-text-tertiary font-medium w-32 align-top">
                            {label}
                          </td>
                          <td className="py-1.5 text-shell-text-secondary break-all">{val}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </div>

        {/* Action bar */}
        <div className="border-t border-white/5 px-5 py-2.5 flex items-center gap-2 shrink-0 flex-wrap">
          {/* Open on YouTube */}
          {selectedItem.source_url && (
            <Button
              variant="outline"
              size="sm"
              className="text-xs gap-1.5"
              onClick={() => window.open(selectedItem.source_url, "_blank", "noopener,noreferrer")}
              aria-label="Open on YouTube"
            >
              <ExternalLink size={12} aria-hidden="true" />
              YouTube
            </Button>
          )}

          {/* Re-ingest */}
          <Button
            variant="outline"
            size="sm"
            className="text-xs gap-1.5"
            onClick={handleReIngest}
            disabled={detailLoading}
            aria-label="Re-ingest this video"
          >
            <RefreshCw size={12} aria-hidden="true" />
            Re-ingest
          </Button>

          {/* Download with quality picker */}
          <div className="relative">
            <Button
              variant="outline"
              size="sm"
              className="text-xs gap-1.5"
              onClick={() => setQualityMenuOpen((prev) => !prev)}
              disabled={downloadStatus.status === "downloading"}
              aria-label="Download video"
              aria-expanded={qualityMenuOpen}
              aria-haspopup="menu"
            >
              {downloadStatus.status === "downloading" ? (
                <>
                  <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  Downloading...
                </>
              ) : downloadStatus.status === "complete" ? (
                <>
                  <Check size={12} aria-hidden="true" />
                  Downloaded
                  {downloadStatus.file_size ? ` (${downloadStatus.file_size})` : ""}
                </>
              ) : (
                <>
                  <Download size={12} aria-hidden="true" />
                  Download
                  <ChevronDown size={10} aria-hidden="true" />
                </>
              )}
            </Button>
            {qualityMenuOpen && downloadStatus.status !== "downloading" && (
              <div
                className="absolute bottom-full left-0 mb-1 z-50 bg-shell-surface border border-white/10 rounded-lg shadow-lg py-1 min-w-[100px]"
                role="menu"
                aria-label="Download quality options"
              >
                {DOWNLOAD_QUALITY_OPTIONS.map((q) => (
                  <button
                    key={q}
                    role="menuitem"
                    className="w-full px-3 py-1.5 text-xs hover:bg-white/5 text-left"
                    onClick={() => startDownload(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Stop monitoring */}
          {(selectedItem.monitor.current_interval ?? 0) > 0 && (
            <Button
              variant="outline"
              size="sm"
              className="text-xs gap-1.5 text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
              aria-label="Stop monitoring this video"
            >
              <MonitorOff size={12} aria-hidden="true" />
              Stop monitoring
            </Button>
          )}

          {/* Delete */}
          <div className="ml-auto">
            {confirmDelete ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-400">Confirm delete?</span>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs border-red-500/40 text-red-400 hover:bg-red-500/15"
                  onClick={handleDelete}
                  aria-label="Confirm delete"
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
                aria-label="Delete this video"
              >
                <Trash2 size={12} aria-hidden="true" />
                Delete
              </Button>
            )}
          </div>
        </div>
      </main>
    );
  })() : null;

  /* ---------------------------------------------------------------- */
  /*  Root render                                                      */
  /* ---------------------------------------------------------------- */

  if (isMobile) {
    if (view === "detail") {
      return (
        <div className="flex flex-col h-full overflow-hidden bg-shell-base">
          {detailViewUI}
        </div>
      );
    }
    return (
      <div className="flex flex-col h-full overflow-hidden bg-shell-base">
        {sidebarUI}
        {listViewUI}
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden bg-shell-base" onClick={() => setQualityMenuOpen(false)}>
      {sidebarUI}
      {view === "list" ? listViewUI : detailViewUI}
    </div>
  );
}
