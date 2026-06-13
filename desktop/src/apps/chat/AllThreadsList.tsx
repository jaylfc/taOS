import { useEffect, useState } from "react";
import { displayAuthor, type AuthorContext } from "./format-author";

interface ThreadSummary {
  id: string;
  author_id: string;
  author_type?: "user" | "agent" | "system";
  content: string;
  reply_count: number;
  last_reply_at: number | null;
}

function relativeTs(ts: number | null): string {
  if (!ts) return "";
  const diff = Date.now() - ts * 1000;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function AllThreadsList({
  channelId,
  onClose,
  onJumpToThread,
  authorCtx = { currentUserId: null, currentUserDisplayName: null },
}: {
  channelId: string;
  onClose: () => void;
  onJumpToThread: (parentId: string) => void;
  authorCtx?: AuthorContext;
}) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`/api/chat/channels/${channelId}/threads`, { signal: ac.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => setThreads(data.threads ?? []))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof Error ? e.message : "failed");
      })
      .finally(() => {
        // ac.signal.aborted is true when we've been superseded
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [channelId]);

  return (
    <aside
      id="all-threads-panel"
      role="complementary"
      aria-label="All threads"
      className="fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 shadow-xl flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <h2 className="text-sm font-semibold">All threads</h2>
        <button onClick={onClose} aria-label="Close" className="text-lg leading-none opacity-60 hover:opacity-100">×</button>
      </header>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading && (
          <div className="px-2 py-4 text-xs text-shell-text-tertiary">Loading…</div>
        )}
        {error && (
          <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-2 mx-2">
            {error}
          </div>
        )}
        {!loading && !error && threads.length === 0 && (
          <div className="px-2 py-4 text-xs text-shell-text-tertiary">No threads yet.</div>
        )}
        {!loading && !error && threads.length > 0 && (
          <ul>
            {threads.map((t) => (
              <li key={t.id}>
                <button
                  className="w-full text-left px-3 py-2.5 rounded hover:bg-white/5 flex flex-col gap-0.5"
                  onClick={() => onJumpToThread(t.id)}
                >
                  <span className="text-xs text-shell-text-secondary line-clamp-2">{t.content}</span>
                  <div className="flex items-center gap-2 text-[11px] text-shell-text-tertiary">
                    <span>@{displayAuthor(t, authorCtx)}</span>
                    <span>·</span>
                    <span>{t.reply_count} {t.reply_count === 1 ? "reply" : "replies"}</span>
                    {t.last_reply_at && (
                      <>
                        <span>·</span>
                        <span>{relativeTs(t.last_reply_at)}</span>
                      </>
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
