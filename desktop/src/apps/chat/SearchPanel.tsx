import { useEffect, useRef, useState } from "react";
import { displayAuthor, type AuthorContext } from "./format-author";

type SearchHit = {
  id: string;
  channel_id: string;
  author_id: string;
  author_type?: "user" | "agent" | "system";
  content: string;
  created_at: number | string;
};

function relativeTime(ts: number | string): string {
  const ms = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : new Date(ts).getTime();
  const diff = Date.now() - ms;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ms).toLocaleDateString();
}

function snippet(content: string, max = 140): string {
  if (content.length <= max) return content;
  return content.slice(0, max - 1) + "…";
}

export function SearchPanel({
  onJump,
  onClose,
  channels = [],
  authorCtx = { currentUserId: null, currentUserDisplayName: null },
}: {
  onJump: (channelId: string, messageId: string) => void;
  onClose: () => void;
  channels?: { id: string; name: string }[];
  authorCtx?: AuthorContext;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setHits([]);
      setLoading(false);
      setError(null);
      return;
    }
    const handle = setTimeout(() => {
      acRef.current?.abort();
      const ac = new AbortController();
      acRef.current = ac;
      setLoading(true);
      setError(null);
      fetch(`/api/chat/search?q=${encodeURIComponent(query)}`, {
        credentials: "include",
        signal: ac.signal,
      })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data: { results?: SearchHit[] }) => setHits(data.results ?? []))
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;
          setError(e instanceof Error ? e.message : "failed");
        })
        .finally(() => {
          if (!ac.signal.aborted) setLoading(false);
        });
    }, 300);
    return () => clearTimeout(handle);
  }, [query]);

  const grouped = new Map<string, SearchHit[]>();
  for (const h of hits) {
    const arr = grouped.get(h.channel_id) ?? [];
    arr.push(h);
    grouped.set(h.channel_id, arr);
  }
  const channelName = (id: string) => channels.find((c) => c.id === id)?.name ?? id;

  return (
    <aside
      id="search-panel"
      role="complementary"
      aria-label="Search messages"
      className="fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 shadow-xl flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10 gap-2">
        <h2 className="text-sm font-semibold">Search</h2>
        <button
          onClick={onClose}
          aria-label="Close"
          className="text-lg leading-none opacity-60 hover:opacity-100"
        >
          ×
        </button>
      </header>
      <div className="px-3 py-2 border-b border-white/10">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search messages…"
          aria-label="Search messages"
          className="w-full bg-shell-bg-deep border border-white/10 rounded px-2 py-1.5 text-sm outline-none focus:border-white/30"
        />
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2 text-sm">
        {query.trim().length < 2 && (
          <div className="px-2 py-4 text-xs text-shell-text-tertiary">Type to search.</div>
        )}
        {error && (
          <div
            role="alert"
            className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-2 mx-2"
          >
            {error}
          </div>
        )}
        {query.trim().length >= 2 && !loading && !error && hits.length === 0 && (
          <div className="px-2 py-4 text-xs text-shell-text-tertiary">No results.</div>
        )}
        {loading && query.trim().length >= 2 && (
          <div className="px-2 py-4 text-xs text-shell-text-tertiary">Searching…</div>
        )}
        {[...grouped.entries()].map(([cid, list]) => (
          <section key={cid} className="mb-2">
            <h3 className="px-2 py-1 text-[11px] uppercase tracking-wide text-white/40">
              {channelName(cid)}
            </h3>
            <ul>
              {list.map((h) => (
                <li key={h.id}>
                  <button
                    type="button"
                    className="w-full text-left px-3 py-2 rounded hover:bg-white/5 flex flex-col gap-0.5"
                    onClick={() => {
                      onJump(h.channel_id, h.id);
                    }}
                  >
                    <span className="text-[11px] text-shell-text-tertiary">
                      @{displayAuthor(h, authorCtx)} · {relativeTime(h.created_at)}
                    </span>
                    <span className="text-xs text-shell-text-secondary line-clamp-3">
                      {snippet(h.content)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </aside>
  );
}
