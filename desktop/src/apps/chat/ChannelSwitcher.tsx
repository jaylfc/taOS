import { useEffect, useRef, useState } from "react";

interface SwitcherChannel {
  id: string;
  name: string;
  type?: string;
}

/**
 * Slack-style quick channel switcher. Cmd/Ctrl+K opens it from MessagesApp;
 * type to filter, Up/Down to move, Enter to select, Escape or backdrop to close.
 */
export function ChannelSwitcher({
  channels,
  onSelect,
  onClose,
}: {
  channels: SwitcherChannel[];
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [highlightIndex, setHighlightIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const q = query.trim().toLowerCase();
  const results = channels
    .filter((c) => (q ? c.name.toLowerCase().includes(q) : true))
    .slice(0, 8);

  // Keep the highlight within the current result list as it shrinks/grows.
  useEffect(() => {
    setHighlightIndex((i) => Math.min(i, Math.max(0, results.length - 1)));
  }, [results.length]);

  const choose = (idx: number) => {
    const hit = results[idx];
    if (hit) {
      onSelect(hit.id);
      onClose();
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIndex((i) => Math.min(i + 1, Math.max(0, results.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(highlightIndex);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[10010] flex items-start justify-center bg-black/50 pt-[18vh]"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Switch channel"
    >
      <div
        className="w-full max-w-[420px] mx-4 bg-zinc-900 border border-white/10 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setHighlightIndex(0);
          }}
          placeholder="Jump to channel…"
          aria-label="Search channels"
          className="w-full bg-transparent px-4 py-3 text-sm text-white outline-none border-b border-white/10 placeholder:text-white/40"
        />
        <ul className="max-h-[320px] overflow-y-auto py-1" role="listbox" aria-label="Channels">
          {results.length === 0 ? (
            <li className="px-4 py-3 text-xs text-white/40">No channels match.</li>
          ) : (
            results.map((c, i) => (
              <li key={c.id} role="option" aria-selected={i === highlightIndex}>
                <button
                  type="button"
                  onClick={() => choose(i)}
                  onMouseEnter={() => setHighlightIndex(i)}
                  className={`w-full text-left px-4 py-2 text-sm flex items-center gap-2 ${
                    i === highlightIndex ? "bg-blue-500/20 text-white" : "text-white/80 hover:bg-white/5"
                  }`}
                >
                  <span className="text-white/40">#</span>
                  <span className="truncate">{c.name}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
