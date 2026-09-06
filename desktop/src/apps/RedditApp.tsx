import { useState, useEffect, useCallback, useRef } from "react";
import {
  ScrollText,
  Lock,
  ChevronLeft,
  ExternalLink,
  Search,
  ChevronDown,
  ChevronRight,
  Plus,
  Check,
  RefreshCw,
  Trash2,
  Bookmark,
  ArrowUp,
  ArrowDown,
  MessageSquare,
  Eye,
  AlignLeft,
} from "lucide-react";
import { Button, Input } from "@/components/ui";
import {
  fetchThread,
  fetchSubreddit,
  searchReddit,
  fetchSaved,
  getAuthStatus,
  saveToLibrary,
} from "@/lib/reddit";
import type {
  RedditPost,
  RedditComment,
  RedditThread,
  RedditListing,
  RedditAuthStatus,
} from "@/lib/reddit";
import { listItems, deleteItem, ingestUrl } from "@/lib/knowledge";
import type { KnowledgeItem } from "@/lib/knowledge";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import "./RedditApp.css";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type View = "feed" | "thread";
type SortMode = "hot" | "new" | "top";
type SidebarSection = "subreddits" | "saved" | "monitored" | "history";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const POPULAR_SUBS = ["LocalLLaMA", "selfhosted", "homelab", "linux"];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function formatScore(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function stripRedditDomain(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?reddit\.com/, "");
}

/* ------------------------------------------------------------------ */
/*  CommentNode (recursive)                                            */
/* ------------------------------------------------------------------ */

interface CommentNodeProps {
  comment: RedditComment;
  maxDepth?: number;
}

function CommentNode({ comment, maxDepth = 4 }: CommentNodeProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [showReplies, setShowReplies] = useState(comment.depth < maxDepth);

  const isDeleted =
    comment.author === "[deleted]" || comment.body === "[deleted]";

  const toggleCollapse = () => setCollapsed((v) => !v);

  return (
    <li role="listitem">
      {/* Comment header */}
      <div className="rd-cmt-head">
        <button
          className="rd-collapse"
          aria-label={collapsed ? "Expand comment" : "Collapse comment"}
          aria-expanded={!collapsed}
          onClick={toggleCollapse}
        >
          {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        </button>
        {isDeleted ? (
          <span className="rd-cmt-meta" style={{ fontStyle: "italic" }}>
            [deleted]
          </span>
        ) : (
          <>
            <span className="rd-cmt-author">u/{comment.author}</span>
            {comment.distinguished === "moderator" && (
              <span className="rd-mod">MOD</span>
            )}
            <span className="rd-cmt-meta">{formatScore(comment.score)} pts</span>
            <span className="rd-cmt-meta">·</span>
            <span className="rd-cmt-meta">{timeAgo(comment.created_utc)}</span>
            {comment.edited && (
              <span className="rd-cmt-meta" style={{ fontStyle: "italic" }}>
                (edited)
              </span>
            )}
          </>
        )}
      </div>

      {/* Comment body */}
      {!collapsed && !isDeleted && (
        <p className="rd-cmt-body" style={{ marginLeft: 20 }}>
          {comment.body}
        </p>
      )}

      {/* Replies */}
      {!collapsed && comment.replies.length > 0 && (
        <div className="rd-cmt-rail">
          <div className="rd-thread-line" />
          <div style={{ flex: 1, minWidth: 0 }}>
            {showReplies || comment.depth < maxDepth ? (
              <ul
                role="list"
                style={{ display: "flex", flexDirection: "column", gap: 4 }}
              >
                {comment.replies.map((r) => (
                  <CommentNode key={r.id} comment={r} maxDepth={maxDepth} />
                ))}
              </ul>
            ) : (
              <button
                className="rd-more-replies"
                onClick={() => setShowReplies(true)}
                aria-label={`Show ${comment.replies.length} more replies`}
              >
                Show {comment.replies.length} more{" "}
                {comment.replies.length === 1 ? "reply" : "replies"}
              </button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  PostCard                                                           */
/* ------------------------------------------------------------------ */

interface PostCardProps {
  post: RedditPost;
  savedItem?: KnowledgeItem;
  onOpen: (post: RedditPost) => void;
  onSave: (post: RedditPost) => void;
  saving: boolean;
}

function PostCard({ post, savedItem, onOpen, onSave, saving }: PostCardProps) {
  const isSaved = !!savedItem;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(post);
    }
  };

  return (
    <article className="rd-post">
      {/* Vote rail — shows the post score with the Reddit upvote affordance */}
      <div className="rd-vote">
        <span className="rd-arrow up" aria-hidden="true">
          <ArrowUp size={17} />
        </span>
        <span className="rd-score">{formatScore(post.score)}</span>
        <span className="rd-arrow" aria-hidden="true">
          <ArrowDown size={17} />
        </span>
      </div>

      <div className="rd-post-body">
        {/* Meta row */}
        <div className="rd-post-meta">
          <span className="rd-chip-sub">r/{post.subreddit}</span>
          <span>u/{post.author}</span>
          <span className="rd-sep">·</span>
          <span>{timeAgo(post.created_utc)}</span>
          {post.flair && <span className="rd-flair">{post.flair}</span>}
        </div>

        {/* Title */}
        <button
          className="rd-post-title"
          onClick={() => onOpen(post)}
          onKeyDown={handleKeyDown}
          tabIndex={0}
          aria-label={`Open thread: ${post.title}`}
        >
          {post.title}
        </button>

        {/* Selftext preview */}
        {post.is_self && post.selftext && (
          <p className="rd-post-excerpt">{post.selftext}</p>
        )}

        {/* Category pills if saved */}
        {isSaved && savedItem.categories.length > 0 && (
          <div className="rd-cat-pills">
            {savedItem.categories.map((cat) => (
              <span key={cat} className="rd-cat">
                {cat}
              </span>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="rd-post-actions">
          <span className="rd-pa">
            <MessageSquare size={13} />
            {post.num_comments} comments
          </span>
          <button
            className={isSaved ? "rd-pa-btn saved" : "rd-pa-btn save-cta"}
            onClick={() => onSave(post)}
            disabled={saving || isSaved}
            aria-label={isSaved ? "Saved to Library" : "Save to Library"}
          >
            {isSaved ? (
              <>
                <Check size={12} />
                Saved
              </>
            ) : (
              <>
                <Bookmark size={12} />
                {saving ? "Saving…" : "Save to Library"}
              </>
            )}
          </button>
        </div>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/*  RedditApp                                                          */
/* ------------------------------------------------------------------ */

export function RedditApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- view ---------- */
  const [view, setView] = useState<View>("feed");
  const [thread, setThread] = useState<RedditThread | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);

  /* ---------- sidebar ---------- */
  const [activeSub, setActiveSub] = useState<string | null>(null);
  const [subs, setSubs] = useState<string[]>(POPULAR_SUBS);
  const [addSubOpen, setAddSubOpen] = useState(false);
  const [newSub, setNewSub] = useState("");
  const [activeSection, setActiveSection] =
    useState<SidebarSection>("subreddits");

  /* ---------- feed ---------- */
  const [listing, setListing] = useState<RedditListing>({
    posts: [],
    after: null,
  });
  const [feedLoading, setFeedLoading] = useState(false);
  const [sort, setSort] = useState<SortMode>("hot");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchInput, setSearchInput] = useState("");

  /* ---------- auth ---------- */
  const [authStatus, setAuthStatus] = useState<RedditAuthStatus>({
    authenticated: false,
  });

  /* ---------- knowledge items (saved) ---------- */
  const [savedItems, setSavedItems] = useState<KnowledgeItem[]>([]);
  const [savingPostId, setSavingPostId] = useState<string | null>(null);

  /* ---------- thread view ---------- */
  const [threadTab, setThreadTab] = useState<
    "comments" | "history" | "metadata"
  >("comments");
  const [threadSaved, setThreadSaved] = useState<KnowledgeItem | null>(null);
  const [threadSaving, setThreadSaving] = useState(false);
  const [confirmDeleteThread, setConfirmDeleteThread] = useState(false);
  const [_monitorEnabled, setMonitorEnabled] = useState(false);

  /* ---------- mobile ---------- */
  const isMobile = useIsMobile();

  const searchRef = useRef<HTMLInputElement>(null);

  /* ---------------------------------------------------------------- */
  /*  Auth + saved items                                               */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    getAuthStatus().then(setAuthStatus);
    refreshSavedItems();
  }, []);

  const refreshSavedItems = useCallback(async () => {
    const { items } = await listItems({ source_type: "reddit", limit: 200 });
    setSavedItems(items);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Feed loading                                                     */
  /* ---------------------------------------------------------------- */

  const loadFeed = useCallback(
    async (sub: string | null, sortMode: SortMode, query: string) => {
      setFeedLoading(true);
      try {
        let result: RedditListing;
        if (query.trim()) {
          result = await searchReddit(query.trim(), sub ?? undefined);
        } else if (activeSection === "saved" && authStatus.authenticated) {
          result = await fetchSaved();
        } else if (sub) {
          result = await fetchSubreddit(sub, sortMode);
        } else {
          result = { posts: [], after: null };
        }
        setListing(result);
      } catch {
        setListing({ posts: [], after: null });
      }
      setFeedLoading(false);
    },
    [activeSection, authStatus.authenticated],
  );

  useEffect(() => {
    if (view === "feed") {
      loadFeed(activeSub, sort, searchQuery);
    }
  }, [activeSub, sort, searchQuery, view, loadFeed]);

  /* ---------------------------------------------------------------- */
  /*  Open thread                                                      */
  /* ---------------------------------------------------------------- */

  const openThread = useCallback(
    async (post: RedditPost) => {
      setView("thread");
      setThreadTab("comments");
      setThread(null);
      setConfirmDeleteThread(false);
      setThreadLoading(true);
      const t = await fetchThread(post.url);
      setThread(t);
      setThreadLoading(false);
      // Check if saved
      const match = savedItems.find(
        (i) =>
          i.source_url === post.url ||
          i.source_url === `https://www.reddit.com${post.permalink}`,
      );
      setThreadSaved(match ?? null);
      setMonitorEnabled(
        match ? (match.monitor?.current_interval ?? 0) > 0 : false,
      );
    },
    [savedItems],
  );

  const goBackToFeed = useCallback(() => {
    setView("feed");
    setThread(null);
    setConfirmDeleteThread(false);
  }, []);

  /* Escape key in thread view */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && view === "thread") goBackToFeed();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [view, goBackToFeed]);

  /* ---------------------------------------------------------------- */
  /*  Save helpers                                                     */
  /* ---------------------------------------------------------------- */

  const handleSavePost = useCallback(
    async (post: RedditPost) => {
      setSavingPostId(post.id);
      await saveToLibrary(post.url, post.title);
      await refreshSavedItems();
      setSavingPostId(null);
    },
    [refreshSavedItems],
  );

  const handleSaveThread = useCallback(async () => {
    if (!thread) return;
    setThreadSaving(true);
    const result = await saveToLibrary(thread.post.url, thread.post.title);
    if (result) {
      await refreshSavedItems();
      const match = savedItems.find((i) => i.source_url === thread.post.url);
      setThreadSaved(match ?? null);
    }
    setThreadSaving(false);
  }, [thread, savedItems, refreshSavedItems]);

  const handleReIngestThread = useCallback(async () => {
    if (!threadSaved) return;
    await ingestUrl(threadSaved.source_url, {
      title: threadSaved.title,
      categories: threadSaved.categories,
    });
    await refreshSavedItems();
  }, [threadSaved, refreshSavedItems]);

  const handleDeleteThread = useCallback(async () => {
    if (!threadSaved) return;
    await deleteItem(threadSaved.id);
    setThreadSaved(null);
    setConfirmDeleteThread(false);
    await refreshSavedItems();
  }, [threadSaved, refreshSavedItems]);

  /* ---------------------------------------------------------------- */
  /*  Add subreddit                                                    */
  /* ---------------------------------------------------------------- */

  const addSub = useCallback(() => {
    const name = newSub.trim().replace(/^r\//, "");
    if (name && !subs.includes(name)) {
      setSubs((prev) => [...prev, name]);
      setActiveSub(name);
      setActiveSection("subreddits");
    }
    setNewSub("");
    setAddSubOpen(false);
  }, [newSub, subs]);

  /* ---------------------------------------------------------------- */
  /*  Saved items helpers                                              */
  /* ---------------------------------------------------------------- */

  const getSavedForPost = useCallback(
    (post: RedditPost): KnowledgeItem | undefined =>
      savedItems.find(
        (i) =>
          i.source_url === post.url ||
          i.source_url === `https://www.reddit.com${post.permalink}`,
      ),
    [savedItems],
  );

  const monitoredItems = savedItems.filter(
    (i) => i.source_type === "reddit" && (i.monitor?.current_interval ?? 0) > 0,
  );

  /* ---------------------------------------------------------------- */
  /*  Sidebar UI                                                       */
  /* ---------------------------------------------------------------- */

  const sidebarUI = (
    <nav
      className="flex flex-col overflow-hidden h-full"
      style={{ background: "var(--color-shell-bg-deep)" }}
      aria-label="Reddit navigation"
    >
      {/* Header — desktop only; MobileSplitView provides its own nav bar on mobile */}
      {!isMobile && (
        <div className="rd-sb-head shrink-0">
          <span className="rd-mark">
            <ScrollText size={14} />
          </span>
          <h1>Reddit</h1>
        </div>
      )}

      <div
        className="flex-1 overflow-y-auto flex flex-col"
        style={{ padding: "14px 10px", gap: 22 }}
      >
        {/* Subreddits */}
        <section>
          <div className="rd-group-h">
            <span>Subreddits</span>
            <button
              className="rd-add"
              aria-label="Add subreddit"
              onClick={() => setAddSubOpen((v) => !v)}
            >
              <Plus size={13} />
            </button>
          </div>

          {addSubOpen && !isMobile && (
            <div className="flex gap-1 mb-1 px-1">
              <Input
                value={newSub}
                onChange={(e) => setNewSub(e.target.value)}
                placeholder="r/subreddit"
                className="h-7 text-xs flex-1"
                aria-label="New subreddit name"
                onKeyDown={(e) => {
                  if (e.key === "Enter") addSub();
                  if (e.key === "Escape") setAddSubOpen(false);
                }}
                autoFocus
              />
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-1.5 text-xs"
                onClick={addSub}
                aria-label="Confirm add subreddit"
              >
                <Check size={11} />
              </Button>
            </div>
          )}

          {subs.map((sub) => {
            const active = activeSection === "subreddits" && activeSub === sub;
            return (
              <button
                key={sub}
                type="button"
                className={active ? "rd-item active" : "rd-item"}
                aria-pressed={active}
                onClick={() => {
                  setActiveSub(sub);
                  setActiveSection("subreddits");
                  setSearchQuery("");
                  setSearchInput("");
                }}
              >
                <span className="rd-rslash">r/</span>
                <span className="rd-label">{sub}</span>
              </button>
            );
          })}
        </section>

        {/* Saved Posts */}
        <section>
          <div className="rd-group-h">
            <span
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              Saved Posts
              {!authStatus.authenticated && <Lock size={10} />}
            </span>
          </div>
          {authStatus.authenticated ? (
            <button
              type="button"
              className={
                activeSection === "saved" ? "rd-item active" : "rd-item"
              }
              aria-pressed={activeSection === "saved"}
              onClick={() => {
                setActiveSection("saved");
                setActiveSub(null);
              }}
            >
              <span className="rd-label">Reddit Saved</span>
            </button>
          ) : (
            <p className="rd-muted">Not connected</p>
          )}
        </section>

        {/* Monitored */}
        <section>
          <div className="rd-group-h">
            <span>Monitored</span>
          </div>
          {monitoredItems.length === 0 ? (
            <p className="rd-muted">None yet</p>
          ) : (
            monitoredItems.map((item) => (
              <button
                key={item.id}
                type="button"
                className={
                  activeSection === "monitored" && activeSub === item.id
                    ? "rd-item active"
                    : "rd-item"
                }
                onClick={() => {
                  setActiveSection("monitored");
                  setActiveSub(item.id);
                }}
                aria-label={`Monitored: ${item.title}`}
              >
                <Eye size={13} style={{ flexShrink: 0 }} />
                <span className="rd-label">{item.title}</span>
              </button>
            ))
          )}
        </section>

        {/* History — desktop only, matches mockup */}
        {!isMobile && (
          <section>
            <div className="rd-group-h">
              <span>History</span>
            </div>
            <p className="rd-muted">Last 30 days of reads</p>
          </section>
        )}
      </div>

      {/* Auth status footer */}
      <div className="rd-foot shrink-0">
        {authStatus.authenticated ? (
          <>
            <span className="rd-ok" />
            <span>u/{authStatus.username}</span>
          </>
        ) : (
          <>
            <span className="rd-off" />
            <a href="/api/reddit/auth/login" aria-label="Connect Reddit account">
              Not connected
            </a>
          </>
        )}
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  Feed view                                                        */
  /* ---------------------------------------------------------------- */

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchQuery(searchInput);
  };

  const feedViewUI = (
    <main className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar: title + search + sort */}
      <div className="rd-toolbar">
        {activeSub && (
          <h2>
            <span className="rd-rslash">r/</span>
            {activeSub}
          </h2>
        )}

        <form onSubmit={handleSearch} className="rd-search" role="search">
          <button type="submit" className="rd-search-submit" aria-label="Run search">
            <Search size={15} />
          </button>
          <input
            ref={searchRef}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={activeSub ? `Search r/${activeSub}…` : "Search Reddit…"}
            aria-label="Search Reddit"
          />
        </form>

        {/* Sort segmented control */}
        <div className="rd-segmented" role="group" aria-label="Sort posts">
          {(["hot", "new", "top"] as SortMode[]).map((s) => (
            <button
              key={s}
              type="button"
              className={sort === s ? "rd-seg on" : "rd-seg"}
              aria-pressed={sort === s}
              onClick={() => setSort(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Post list */}
      <div className="rd-feed">
        {feedLoading && (
          <>
            <div className="rd-skeleton" aria-hidden="true" />
            <div className="rd-skeleton" aria-hidden="true" />
            <div className="rd-skeleton" aria-hidden="true" />
            <span className="sr-only" role="status">
              Loading posts
            </span>
          </>
        )}

        {!feedLoading &&
          !activeSub &&
          activeSection === "subreddits" &&
          !searchQuery && (
            <div className="rd-empty">
              <ScrollText size={36} style={{ opacity: 0.3 }} />
              <p>Select a subreddit to browse</p>
            </div>
          )}

        {!feedLoading && listing.posts.length > 0 && (
          <ul
            role="list"
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            {listing.posts.map((post) => (
              <li key={post.id} role="listitem">
                <PostCard
                  post={post}
                  savedItem={getSavedForPost(post)}
                  onOpen={openThread}
                  onSave={handleSavePost}
                  saving={savingPostId === post.id}
                />
              </li>
            ))}
          </ul>
        )}

        {!feedLoading &&
          listing.posts.length === 0 &&
          (activeSub || searchQuery || activeSection === "saved") && (
            <div className="rd-empty">
              <p>No posts found</p>
            </div>
          )}
      </div>
    </main>
  );

  /* ---------------------------------------------------------------- */
  /*  Thread view                                                      */
  /* ---------------------------------------------------------------- */

  const threadViewUI = (() => {
    const post = thread?.post ?? null;
    const comments = thread?.comments ?? [];

    return (
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Back button + action bar */}
        <div className="rd-thread-bar">
          <button
            className="rd-back"
            onClick={goBackToFeed}
            aria-label="Back to feed"
          >
            <ChevronLeft size={15} />
            Back to feed
          </button>

          {post && (
            <div className="rd-thread-actions">
              <a
                href={`https://www.reddit.com${post.permalink}`}
                target="_blank"
                rel="noopener noreferrer"
                className="rd-t-btn"
                aria-label="Open on Reddit"
              >
                <ExternalLink size={13} />
                Reddit
              </a>

              {threadSaved ? (
                <>
                  <button
                    className="rd-t-btn is-saved"
                    disabled
                    aria-label="Already saved to Library"
                  >
                    <Check size={13} />
                    Saved
                  </button>
                  <button
                    className="rd-t-btn"
                    onClick={handleReIngestThread}
                    aria-label="Re-ingest this thread"
                  >
                    <RefreshCw size={13} />
                    Re-ingest
                  </button>
                  {confirmDeleteThread ? (
                    <>
                      <button
                        className="rd-t-btn danger"
                        onClick={handleDeleteThread}
                        aria-label="Confirm delete from Library"
                      >
                        Confirm Delete
                      </button>
                      <button
                        className="rd-t-btn"
                        onClick={() => setConfirmDeleteThread(false)}
                        aria-label="Cancel delete"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      className="rd-t-btn danger"
                      onClick={() => setConfirmDeleteThread(true)}
                      aria-label="Delete from Library"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </>
              ) : (
                <button
                  className="rd-t-btn"
                  onClick={handleSaveThread}
                  disabled={threadSaving}
                  aria-label="Save to Library"
                >
                  <Bookmark size={13} />
                  {threadSaving ? "Saving…" : "Save to Library"}
                </button>
              )}
            </div>
          )}
        </div>

        <div className="rd-thread">
          {threadLoading && (
            <div className="rd-spin">
              <RefreshCw size={20} className="animate-spin" />
            </div>
          )}

          {!threadLoading && post && (
            <div className="rd-thread-inner">
              {/* Post header */}
              <h1 className="rd-thread-title">{post.title}</h1>

              <div className="rd-thread-meta">
                <span className="rd-chip-sub">r/{post.subreddit}</span>
                <span>by u/{post.author}</span>
                <span className="rd-sep">·</span>
                <span>{formatScore(post.score)} pts</span>
                <span className="rd-sep">·</span>
                <span className="rd-ratio">
                  {Math.round(post.upvote_ratio * 100)}% upvoted
                </span>
                <span className="rd-sep">·</span>
                <span>{timeAgo(post.created_utc)}</span>
              </div>

              {/* Summary if saved */}
              {threadSaved?.summary && (
                <div className="rd-summary">
                  <div className="rd-lbl">
                    <AlignLeft size={11} />
                    Library summary
                  </div>
                  <p>{threadSaved.summary}</p>
                </div>
              )}

              {/* Post body */}
              {post.is_self && post.selftext && (
                <div className="rd-selftext">{post.selftext}</div>
              )}

              {!post.is_self && (
                <a
                  href={post.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rd-extlink"
                  aria-label={`External link: ${post.url}`}
                >
                  <ExternalLink size={12} />
                  {stripRedditDomain(post.url) || post.url}
                </a>
              )}

              {/* Tabs */}
              <div
                role="tablist"
                aria-label="Thread sections"
                className="rd-tabs"
              >
                {(["comments", "history", "metadata"] as const).map((tab) => (
                  <button
                    key={tab}
                    role="tab"
                    aria-selected={threadTab === tab}
                    onClick={() => setThreadTab(tab)}
                    className={threadTab === tab ? "rd-tab on" : "rd-tab"}
                  >
                    {tab}
                    {tab === "comments" && (
                      <span className="rd-badge">{post.num_comments}</span>
                    )}
                  </button>
                ))}
              </div>

              {/* Comments tab */}
              {threadTab === "comments" && (
                <ul role="list" className="rd-comments">
                  {comments.length === 0 ? (
                    <li
                      className="rd-muted"
                      style={{ padding: "16px 0", textAlign: "center" }}
                    >
                      No comments yet
                    </li>
                  ) : (
                    comments.map((c) => <CommentNode key={c.id} comment={c} />)
                  )}
                </ul>
              )}

              {/* History tab */}
              {threadTab === "history" && (
                <div className="rd-muted" style={{ padding: "16px 0" }}>
                  {threadSaved ? (
                    <p>Monitoring snapshots will appear here when available.</p>
                  ) : (
                    <p>Save this thread to the Library to enable monitoring.</p>
                  )}
                </div>
              )}

              {/* Metadata tab */}
              {threadTab === "metadata" && post && (
                <dl className="rd-meta-grid">
                  {(
                    [
                      ["Subreddit", `r/${post.subreddit}`],
                      ["Author", `u/${post.author}`],
                      ["Score", formatScore(post.score)],
                      [
                        "Upvote ratio",
                        `${Math.round(post.upvote_ratio * 100)}%`,
                      ],
                      ["Comments", String(post.num_comments)],
                      ["Flair", post.flair || "—"],
                      ["Type", post.is_self ? "Text post" : "Link post"],
                      [
                        "Posted",
                        new Date(post.created_utc * 1000).toLocaleString(),
                      ],
                      ["Permalink", post.permalink],
                    ] as [string, string][]
                  ).map(([label, value]) => (
                    <div key={label} className="contents">
                      <dt>{label}</dt>
                      <dd title={value}>{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          )}

          {!threadLoading && !thread && (
            <div className="rd-empty">
              <p>Failed to load thread</p>
            </div>
          )}
        </div>
      </main>
    );
  })();

  /* ---------------------------------------------------------------- */
  /*  Layout                                                           */
  /* ---------------------------------------------------------------- */

  // On mobile, only slide to detail pane when a subreddit is actively selected.
  // On desktop, keep existing behaviour (activeSub drives the detail pane).
  const splitSelectedId = activeSub;

  const handleSplitBack = useCallback(() => {
    setActiveSub(null);
    setView("feed");
    setThread(null);
    setConfirmDeleteThread(false);
  }, []);

  // Detail content: when in thread view show threadViewUI, otherwise feedViewUI
  const detailContent = view === "thread" ? threadViewUI : feedViewUI;

  return (
    <div className="reddit-app flex flex-col h-full min-h-0 overflow-hidden bg-shell-base text-shell-text relative">
      <MobileSplitView
        selectedId={splitSelectedId}
        onBack={handleSplitBack}
        listTitle="Reddit"
        detailTitle={activeSub ? `r/${activeSub}` : undefined}
        listWidth={224}
        list={sidebarUI}
        detail={
          splitSelectedId !== null ? (
            detailContent
          ) : !isMobile ? (
            <div className="rd-empty" style={{ height: "100%" }}>
              <ScrollText size={36} style={{ opacity: 0.2 }} />
              <p>Select a subreddit</p>
            </div>
          ) : null
        }
      />

      {/* Add subreddit modal — bottom sheet on mobile */}
      {isMobile && addSubOpen && (
        <div
          className="absolute inset-0 z-50 flex items-end bg-black/50 backdrop-blur-sm"
          onClick={() => setAddSubOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Add subreddit"
        >
          <div
            style={{
              borderRadius: "20px 20px 0 0",
              width: "100%",
              background: "var(--color-shell-bg)",
              borderTop: "1px solid var(--color-shell-border)",
              padding: "20px 16px calc(32px + env(safe-area-inset-bottom))",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm font-semibold mb-3">Add Subreddit</p>
            <div className="flex gap-2">
              <Input
                value={newSub}
                onChange={(e) => setNewSub(e.target.value)}
                placeholder="r/subreddit"
                className="flex-1"
                aria-label="New subreddit name"
                onKeyDown={(e) => {
                  if (e.key === "Enter") addSub();
                  if (e.key === "Escape") setAddSubOpen(false);
                }}
                autoFocus
              />
              <Button onClick={addSub} aria-label="Confirm add subreddit">
                <Check size={14} />
                Add
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
