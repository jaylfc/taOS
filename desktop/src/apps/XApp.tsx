import { useState, useEffect, useCallback } from "react";
import {
  AtSign,
  ChevronLeft,
  ExternalLink,
  Heart,
  RefreshCw,
  Repeat2,
  Eye,
  Plus,
  Trash2,
  Bell,
  BookMarked,
  Image,
  Clock,
  MonitorCheck,
  Edit3,
  Save,
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
import {
  fetchTweet,
  fetchThread,
  getAuthStatus,
  listWatches,
  createWatch,
  updateWatch,
  deleteWatch,
  saveToLibrary,
} from "@/lib/x-monitor";
import type { Tweet, XThread, AuthorWatch, XAuthStatus } from "@/lib/x-monitor";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type View = "list" | "detail";
type DetailTab = "thread" | "history" | "metadata";

interface WatchFormState {
  handle: string;
  all_posts: boolean;
  min_likes: number;
  threads_only: boolean;
  media_only: boolean;
  frequency: number;
}

const DEFAULT_WATCH_FORM: WatchFormState = {
  handle: "",
  all_posts: true,
  min_likes: 0,
  threads_only: false,
  media_only: false,
  frequency: 1800,
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const timeAgo = (ts: number): string => {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
};

const fmtCount = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
};

const extractTweetId = (input: string): string | null => {
  const m = input.match(/(?:twitter\.com|x\.com)\/\w+\/status\/(\d+)/);
  if (m) return m[1] ?? null;
  if (/^\d+$/.test(input.trim())) return input.trim();
  return null;
};

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function EngagementBar({ tweet }: { tweet: Tweet }) {
  return (
    <div
      className="flex items-center gap-4 text-xs text-shell-text-tertiary"
      aria-label="Engagement stats"
    >
      <span className="flex items-center gap-1" aria-label={`${fmtCount(tweet.likes)} likes`}>
        <Heart size={11} aria-hidden="true" />
        {fmtCount(tweet.likes)}
      </span>
      <span className="flex items-center gap-1" aria-label={`${fmtCount(tweet.reposts)} reposts`}>
        <Repeat2 size={11} aria-hidden="true" />
        {fmtCount(tweet.reposts)}
      </span>
      <span className="flex items-center gap-1" aria-label={`${fmtCount(tweet.views)} views`}>
        <Eye size={11} aria-hidden="true" />
        {fmtCount(tweet.views)}
      </span>
    </div>
  );
}

function TweetCard({
  tweet,
  onClick,
}: {
  tweet: Tweet;
  onClick: () => void;
}) {
  const hasMedia = tweet.media.length > 0;
  return (
    <Card
      className="cursor-pointer hover:bg-white/5 transition-colors border border-white/5"
      role="button"
      tabIndex={0}
      aria-label={`Tweet by @${tweet.handle}: ${tweet.text.slice(0, 60)}`}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <CardContent className="p-3 space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-accent">@{tweet.handle}</span>
          <div className="flex items-center gap-1.5">
            {hasMedia && (
              <span
                className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-accent/10 text-accent border border-accent/20"
                aria-label="Has media"
              >
                <Image size={9} aria-hidden="true" />
                media
              </span>
            )}
            <span className="text-[10px] text-shell-text-tertiary flex items-center gap-0.5">
              <Clock size={9} aria-hidden="true" />
              {timeAgo(tweet.created_at)}
            </span>
          </div>
        </div>
        <p className="text-xs text-shell-text line-clamp-2">{tweet.text}</p>
        <EngagementBar tweet={tweet} />
      </CardContent>
    </Card>
  );
}

function WatchForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: WatchFormState;
  onSave: (form: WatchFormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<WatchFormState>(initial ?? DEFAULT_WATCH_FORM);

  const handleChange = (key: keyof WatchFormState, value: string | boolean | number) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave(form);
  };

  const FREQ_OPTIONS = [
    { label: "15 min", value: 900 },
    { label: "30 min", value: 1800 },
    { label: "1 hour", value: 3600 },
    { label: "6 hours", value: 21600 },
  ];

  return (
    <form
      onSubmit={handleSubmit}
      className="p-2 space-y-2 bg-shell-surface/50 rounded-md border border-white/10"
      aria-label={initial ? "Edit watch" : "Add watch"}
    >
      {!initial && (
        <div>
          <label htmlFor="watch-handle" className="text-[10px] text-shell-text-tertiary block mb-0.5">
            Handle
          </label>
          <Input
            id="watch-handle"
            placeholder="@handle"
            value={form.handle}
            onChange={(e) => handleChange("handle", e.target.value)}
            className="h-6 text-xs"
            required
          />
        </div>
      )}

      {/* Filter toggles */}
      <fieldset className="space-y-1">
        <legend className="text-[10px] text-shell-text-tertiary mb-0.5">Filters</legend>

        {(
          [
            { key: "all_posts", label: "All posts" },
            { key: "threads_only", label: "Threads only" },
            { key: "media_only", label: "Media only" },
          ] as { key: keyof WatchFormState; label: string }[]
        ).map(({ key, label }) => (
          <label key={key} className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={!!form[key]}
              onChange={(e) => handleChange(key, e.target.checked)}
              className="accent-accent w-3 h-3"
              aria-label={label}
            />
            <span className="text-[10px]">{label}</span>
          </label>
        ))}

        <div>
          <label htmlFor="min-likes" className="text-[10px] text-shell-text-tertiary block mb-0.5">
            Min likes
          </label>
          <Input
            id="min-likes"
            type="number"
            min={0}
            value={form.min_likes}
            onChange={(e) => handleChange("min_likes", Number(e.target.value))}
            className="h-6 text-xs w-20"
            aria-label="Minimum likes threshold"
          />
        </div>
      </fieldset>

      {/* Frequency picker */}
      <div>
        <label htmlFor="watch-frequency" className="text-[10px] text-shell-text-tertiary block mb-0.5">
          Check every
        </label>
        <select
          id="watch-frequency"
          value={form.frequency}
          onChange={(e) => handleChange("frequency", Number(e.target.value))}
          className="h-6 text-xs bg-shell-surface border border-white/10 rounded px-1 text-shell-text"
          aria-label="Check frequency"
        >
          {FREQ_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-1.5 pt-1">
        <Button
          type="submit"
          size="sm"
          className="h-6 text-[10px] gap-1"
        >
          <Save size={10} aria-hidden="true" />
          Save
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          className="h-6 text-[10px]"
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}

function WatchRow({
  watch,
  onDelete,
  onUpdate,
}: {
  watch: AuthorWatch;
  onDelete: (handle: string) => void;
  onUpdate: (handle: string, form: WatchFormState) => void;
}) {
  const [editing, setEditing] = useState(false);

  const handleUpdate = (form: WatchFormState) => {
    onUpdate(watch.handle, form);
    setEditing(false);
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between group px-1">
        <div className="flex flex-col min-w-0">
          <span className="text-xs font-medium truncate">@{watch.handle}</span>
          <span className="text-[10px] text-shell-text-tertiary">
            {timeAgo(watch.last_check)}
          </span>
        </div>
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          <Button
            variant="ghost"
            size="sm"
            className="h-5 w-5 p-0"
            aria-label={`Edit watch for @${watch.handle}`}
            onClick={() => setEditing((v) => !v)}
          >
            <Edit3 size={10} aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 w-5 p-0 text-red-400 hover:text-red-300"
            aria-label={`Delete watch for @${watch.handle}`}
            onClick={() => onDelete(watch.handle)}
          >
            <Trash2 size={10} aria-hidden="true" />
          </Button>
        </div>
      </div>
      {editing && (
        <WatchForm
          initial={{
            handle: watch.handle,
            all_posts: watch.filters.all_posts ?? true,
            min_likes: watch.filters.min_likes ?? 0,
            threads_only: watch.filters.threads_only ?? false,
            media_only: watch.filters.media_only ?? false,
            frequency: watch.frequency,
          }}
          onSave={handleUpdate}
          onCancel={() => setEditing(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main App                                                           */
/* ------------------------------------------------------------------ */

export function XApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- layout ---------- */
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  /* ---------- view state ---------- */
  const [view, setView] = useState<View>("list");
  const [selectedThread, setSelectedThread] = useState<XThread | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("thread");

  /* ---------- list state ---------- */
  const [items, setItems] = useState<Tweet[]>([]);
  const [listLoading, setListLoading] = useState(true);

  /* ---------- URL bar ---------- */
  const [urlInput, setUrlInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  /* ---------- watches ---------- */
  const [watches, setWatches] = useState<AuthorWatch[]>([]);
  const [showWatchForm, setShowWatchForm] = useState(false);

  /* ---------- auth ---------- */
  const [authStatus, setAuthStatus] = useState<XAuthStatus>({ authenticated: false });

  /* ---------- detail ---------- */
  const [detailLoading, setDetailLoading] = useState(false);
  const [refetching, setRefetching] = useState(false);

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const loadItems = useCallback(async () => {
    setListLoading(true);
    try {
      const res = await fetch("/api/knowledge/items?source_type=x&limit=50", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          const raw: { metadata?: Record<string, unknown>; source_id?: string; content?: string; created_at?: number }[] =
            Array.isArray(data) ? data : (data.items ?? []);
          // Map KnowledgeItems to Tweet shape using metadata
          setItems(
            raw.map((item) => ({
              id: item.source_id ?? "",
              author: String(item.metadata?.author ?? ""),
              handle: String(item.metadata?.handle ?? ""),
              text: item.content ?? "",
              likes: Number(item.metadata?.likes ?? 0),
              reposts: Number(item.metadata?.reposts ?? 0),
              views: Number(item.metadata?.views ?? 0),
              created_at: Number(item.metadata?.created_at ?? item.created_at ?? 0),
              media: [],
            })),
          );
        }
      }
    } catch {
      setItems([]);
    }
    setListLoading(false);
  }, []);

  const loadWatches = useCallback(async () => {
    const w = await listWatches();
    setWatches(w);
  }, []);

  const loadAuthStatus = useCallback(async () => {
    const status = await getAuthStatus();
    setAuthStatus(status);
  }, []);

  useEffect(() => {
    loadItems();
    loadWatches();
    loadAuthStatus();
  }, [loadItems, loadWatches, loadAuthStatus]);

  /* ---------------------------------------------------------------- */
  /*  URL bar — save tweet                                             */
  /* ---------------------------------------------------------------- */

  const handleSave = useCallback(async () => {
    setSaveError("");
    const tweetId = extractTweetId(urlInput.trim());
    if (!tweetId) {
      setSaveError("Paste a tweet URL or numeric ID");
      return;
    }

    setSaving(true);
    try {
      // First fetch the tweet data
      const tweet = await fetchTweet(tweetId);
      if (!tweet) {
        setSaveError("Could not fetch tweet — yt-dlp may not be installed");
        setSaving(false);
        return;
      }

      // Save to knowledge library
      const url = `https://twitter.com/i/web/status/${tweetId}`;
      await saveToLibrary(url);
      setUrlInput("");
      await loadItems();
    } catch {
      setSaveError("Failed to save tweet");
    }
    setSaving(false);
  }, [urlInput, loadItems]);

  /* ---------------------------------------------------------------- */
  /*  Detail view                                                     */
  /* ---------------------------------------------------------------- */

  const openDetail = useCallback(async (tweet: Tweet) => {
    setDetailLoading(true);
    setView("detail");
    setDetailTab("thread");
    try {
      const thread = await fetchThread(tweet.id);
      setSelectedThread(thread ?? { tweets: [tweet], text: `@${tweet.handle}\n${tweet.text}` });
    } catch {
      setSelectedThread({ tweets: [tweet], text: `@${tweet.handle}\n${tweet.text}` });
    }
    setDetailLoading(false);
  }, []);

  const handleRefetch = useCallback(async () => {
    if (!selectedThread?.tweets[0]) return;
    setRefetching(true);
    try {
      const thread = await fetchThread(selectedThread.tweets[0].id);
      if (thread) setSelectedThread(thread);
    } catch {
      /* ignore */
    }
    setRefetching(false);
  }, [selectedThread]);

  const handleDeleteDetail = useCallback(async () => {
    if (!selectedThread?.tweets[0]) return;
    try {
      const tweetId = selectedThread.tweets[0].id;
      // Remove from knowledge store (best-effort)
      await fetch(`/api/knowledge/items?source_id=${tweetId}`, { method: "DELETE" });
    } catch {
      /* ignore */
    }
    setView("list");
    setSelectedThread(null);
    await loadItems();
  }, [selectedThread, loadItems]);

  /* ---------------------------------------------------------------- */
  /*  Watch management                                                 */
  /* ---------------------------------------------------------------- */

  const handleCreateWatch = useCallback(
    async (form: WatchFormState) => {
      await createWatch(
        form.handle,
        {
          all_posts: form.all_posts,
          min_likes: form.min_likes,
          threads_only: form.threads_only,
          media_only: form.media_only,
        },
        form.frequency,
      );
      setShowWatchForm(false);
      await loadWatches();
    },
    [loadWatches],
  );

  const handleUpdateWatch = useCallback(
    async (handle: string, form: WatchFormState) => {
      await updateWatch(handle, {
        filters: {
          all_posts: form.all_posts,
          min_likes: form.min_likes,
          threads_only: form.threads_only,
          media_only: form.media_only,
        },
        frequency: form.frequency,
      });
      await loadWatches();
    },
    [loadWatches],
  );

  const handleDeleteWatch = useCallback(
    async (handle: string) => {
      await deleteWatch(handle);
      await loadWatches();
    },
    [loadWatches],
  );

  /* ---------------------------------------------------------------- */
  /*  Sidebar                                                          */
  /* ---------------------------------------------------------------- */

  const sidebarUI = (
    <nav
      className={
        isMobile
          ? "w-full flex flex-col overflow-hidden h-full"
          : "w-52 shrink-0 border-r border-white/5 bg-shell-surface/30 flex flex-col overflow-hidden"
      }
      aria-label="X Monitor navigation"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-3 border-b border-white/5 shrink-0">
        <AtSign size={15} className="text-accent" aria-hidden="true" />
        <h1 className="text-sm font-semibold">X Monitor</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-4">

        {/* Watched Authors */}
        <section aria-labelledby="watched-authors-heading">
          <div className="flex items-center justify-between mb-1 px-1">
            <span
              id="watched-authors-heading"
              className="text-[10px] uppercase tracking-wider text-shell-text-tertiary font-semibold"
            >
              Watched Authors
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-5 w-5 p-0"
              aria-label="Add watched author"
              onClick={() => setShowWatchForm((v) => !v)}
            >
              <Plus size={10} aria-hidden="true" />
            </Button>
          </div>

          {showWatchForm && (
            <WatchForm
              onSave={handleCreateWatch}
              onCancel={() => setShowWatchForm(false)}
            />
          )}

          {watches.length === 0 && !showWatchForm && (
            <p className="text-[10px] text-shell-text-tertiary px-1">No watched authors</p>
          )}

          <ul className="space-y-1" aria-label="Watched authors list">
            {watches.map((watch) => (
              <li key={watch.handle}>
                <WatchRow
                  watch={watch}
                  onDelete={handleDeleteWatch}
                  onUpdate={handleUpdateWatch}
                />
              </li>
            ))}
          </ul>
        </section>

        {/* Saved Threads */}
        <section aria-labelledby="saved-threads-heading">
          <div className="px-1 mb-1">
            <span
              id="saved-threads-heading"
              className="text-[10px] uppercase tracking-wider text-shell-text-tertiary font-semibold"
            >
              Saved Threads
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-xs h-7 px-2 gap-1.5"
            onClick={() => setView("list")}
            aria-pressed={view === "list"}
          >
            <BookMarked size={11} aria-hidden="true" />
            All Saved
            {items.length > 0 && (
              <span
                className="ml-auto text-[10px] tabular-nums text-shell-text-tertiary"
                aria-label={`${items.length} items`}
              >
                {items.length}
              </span>
            )}
          </Button>
        </section>

        {/* Monitored */}
        <section aria-labelledby="monitored-heading">
          <div className="px-1 mb-1">
            <span
              id="monitored-heading"
              className="text-[10px] uppercase tracking-wider text-shell-text-tertiary font-semibold"
            >
              Monitored
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-xs h-7 px-2 gap-1.5"
            disabled
          >
            <MonitorCheck size={11} aria-hidden="true" />
            Active monitors
          </Button>
        </section>

        {/* Auth status */}
        <section className="px-1 pt-1 border-t border-white/5" aria-label="Authentication status">
          <p className="text-[10px] text-shell-text-tertiary leading-relaxed">
            {authStatus.authenticated
              ? `Connected as @${authStatus.handle}`
              : "Not connected — log in via Browsers app"}
          </p>
        </section>
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  List view                                                        */
  /* ---------------------------------------------------------------- */

  const listUI = (
    <main className="flex-1 flex flex-col overflow-hidden" aria-label="Saved tweets">
      {/* URL bar */}
      <div className="shrink-0 px-3 py-2 border-b border-white/5 flex gap-2 items-center">
        <label htmlFor="tweet-url-input" className="sr-only">
          Paste tweet URL to save
        </label>
        <Input
          id="tweet-url-input"
          placeholder="Paste tweet URL to save..."
          value={urlInput}
          onChange={(e) => {
            setUrlInput(e.target.value);
            setSaveError("");
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSave();
          }}
          className="flex-1 h-7 text-xs"
          aria-describedby={saveError ? "save-error" : undefined}
        />
        <Button
          size="sm"
          className="h-7 text-xs px-3"
          onClick={handleSave}
          disabled={saving || !urlInput.trim()}
          aria-label="Save tweet"
        >
          {saving ? (
            <RefreshCw size={12} className="animate-spin" aria-hidden="true" />
          ) : (
            "Save"
          )}
        </Button>
      </div>
      {saveError && (
        <p id="save-error" className="px-3 py-1 text-[11px] text-red-400" role="alert">
          {saveError}
        </p>
      )}

      {/* Item list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {listLoading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw size={16} className="animate-spin text-shell-text-tertiary" aria-label="Loading" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 gap-2 text-shell-text-tertiary">
            <AtSign size={24} aria-hidden="true" />
            <p className="text-xs">No saved tweets yet</p>
            <p className="text-[11px]">Paste a tweet URL above to save it</p>
          </div>
        ) : (
          items.map((tweet) => (
            <TweetCard key={tweet.id} tweet={tweet} onClick={() => openDetail(tweet)} />
          ))
        )}
      </div>
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Detail view                                                      */
  /* ---------------------------------------------------------------- */

  const primaryTweet = selectedThread?.tweets[0] ?? null;

  const detailUI = (
    <main className="flex-1 flex flex-col overflow-hidden" aria-label="Tweet detail">
      {/* Back button */}
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-white/5">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 gap-1 text-xs"
          onClick={() => {
            setView("list");
            setSelectedThread(null);
          }}
          aria-label="Back to list"
        >
          <ChevronLeft size={13} aria-hidden="true" />
          Back
        </Button>
        {primaryTweet && (
          <span className="text-xs text-shell-text-tertiary">@{primaryTweet.handle}</span>
        )}
        {detailLoading && (
          <RefreshCw size={12} className="animate-spin text-shell-text-tertiary ml-auto" aria-label="Loading thread" />
        )}
      </div>

      {detailLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <RefreshCw size={20} className="animate-spin text-shell-text-tertiary" aria-label="Loading" />
        </div>
      ) : !selectedThread || selectedThread.tweets.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-xs text-shell-text-tertiary">Could not load thread</p>
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Engagement stats bar */}
          {primaryTweet && (
            <div className="shrink-0 px-4 py-2 border-b border-white/5 flex items-center gap-4">
              <EngagementBar tweet={primaryTweet} />
              <span className="text-[10px] text-shell-text-tertiary ml-auto flex items-center gap-0.5">
                <Clock size={9} aria-hidden="true" />
                {timeAgo(primaryTweet.created_at)}
              </span>
            </div>
          )}

          {/* Tabs */}
          <Tabs
            value={detailTab}
            onValueChange={(v) => setDetailTab(v as DetailTab)}
            className="flex-1 flex flex-col overflow-hidden"
          >
            <TabsList className="shrink-0 px-3 pt-2 border-b border-white/5 bg-transparent justify-start gap-1 h-auto pb-0">
              <TabsTrigger value="thread" className="text-xs pb-1.5">Thread</TabsTrigger>
              <TabsTrigger value="history" className="text-xs pb-1.5">History</TabsTrigger>
              <TabsTrigger value="metadata" className="text-xs pb-1.5">Metadata</TabsTrigger>
            </TabsList>

            <div className="flex-1 overflow-y-auto">
              <TabsContent value="thread" className="p-4 space-y-4 mt-0">
                {selectedThread.tweets.map((tweet) => (
                  <article key={tweet.id} className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-accent">@{tweet.handle}</span>
                      <span className="text-[10px] text-shell-text-tertiary">
                        {timeAgo(tweet.created_at)}
                      </span>
                    </div>
                    <p className="text-sm text-shell-text leading-relaxed whitespace-pre-wrap">
                      {tweet.text}
                    </p>
                    {tweet.media.length > 0 && (
                      <div className="flex flex-wrap gap-1" aria-label="Media attachments">
                        {tweet.media.slice(0, 4).map((m, i) => (
                          <a
                            key={i}
                            href={m.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 flex items-center gap-0.5"
                            aria-label={`${m.type} attachment ${i + 1}`}
                          >
                            <Image size={9} aria-hidden="true" />
                            {m.type}
                          </a>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
              </TabsContent>

              <TabsContent value="history" className="p-4 mt-0">
                <p className="text-xs text-shell-text-tertiary">
                  Engagement history tracking is available for monitored tweets.
                </p>
              </TabsContent>

              <TabsContent value="metadata" className="p-4 mt-0">
                {primaryTweet && (
                  <dl className="space-y-2 text-xs">
                    {(
                      [
                        ["Tweet ID", primaryTweet.id],
                        ["Author", primaryTweet.author],
                        ["Handle", `@${primaryTweet.handle}`],
                        ["Likes", String(primaryTweet.likes)],
                        ["Reposts", String(primaryTweet.reposts)],
                        ["Views", String(primaryTweet.views)],
                        ["Created", new Date(primaryTweet.created_at * 1000).toLocaleString()],
                        ["Thread length", String(selectedThread.tweets.length)],
                      ] as [string, string][]
                    ).map(([key, val]) => (
                      <div key={key} className="flex gap-2">
                        <dt className="text-shell-text-tertiary w-24 shrink-0">{key}</dt>
                        <dd className="text-shell-text font-mono text-[11px] break-all">{val}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </TabsContent>
            </div>
          </Tabs>

          {/* Action bar */}
          <div
            className="shrink-0 flex items-center gap-1.5 px-3 py-2 border-t border-white/5"
            role="toolbar"
            aria-label="Tweet actions"
          >
            {primaryTweet && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs gap-1"
                aria-label="Open tweet on X"
                onClick={() =>
                  window.open(
                    `https://twitter.com/i/web/status/${primaryTweet.id}`,
                    "_blank",
                    "noopener,noreferrer",
                  )
                }
              >
                <ExternalLink size={11} aria-hidden="true" />
                Open on X
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={refetching}
              aria-label="Re-fetch tweet"
              onClick={handleRefetch}
            >
              <RefreshCw size={11} className={refetching ? "animate-spin" : ""} aria-hidden="true" />
              Re-fetch
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled
              aria-label="Monitor this tweet (requires auth)"
              title="Requires X authentication via Browsers app"
            >
              <Bell size={11} aria-hidden="true" />
              Monitor
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs gap-1 text-red-400 hover:text-red-300 ml-auto"
              aria-label="Delete saved tweet"
              onClick={handleDeleteDetail}
            >
              <Trash2 size={11} aria-hidden="true" />
              Delete
            </Button>
          </div>
        </div>
      )}
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  if (isMobile) {
    if (view === "detail") return detailUI;
    if (view === "list") {
      return (
        <div className="flex h-full flex-col bg-shell-bg text-shell-text select-none">
          {listUI}
        </div>
      );
    }
    return (
      <div className="flex h-full flex-col bg-shell-bg text-shell-text select-none">
        {sidebarUI}
      </div>
    );
  }

  return (
    <div className="flex h-full bg-shell-bg text-shell-text select-none overflow-hidden">
      {sidebarUI}
      {view === "list" ? listUI : detailUI}
    </div>
  );
}
