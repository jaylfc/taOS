/**
 * Find-in-page overlay. Triggered by Cmd+F.
 *
 * For PR 4: renders the search UI and posts a message to the active
 * iframe ("taos-find:query"). copilot.js (PR 6) will respond by
 * highlighting matches and tracking match count via "taos-find:result"
 * back-messages. Until PR 6 lands, the iframe doesn't respond and the
 * UI shows "0 matches" — that's expected.
 *
 * Esc and the close button dismiss the overlay.
 */
import { useEffect, useRef, useState } from "react";
import { Search, X, ChevronUp, ChevronDown } from "lucide-react";

interface FindInPageProps {
  windowId: string;
  onClose: () => void;
}

export function FindInPage({ windowId, onClose }: FindInPageProps) {
  const [query, setQuery] = useState("");
  const [matchCount, setMatchCount] = useState<number | null>(null);
  const [matchIndex, setMatchIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Auto-focus on mount
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  // Listen for taos-find:result events from the iframe (PR 6 will dispatch)
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e?.data?.type !== "taos-find:result") return;
      if (e.data.windowId !== windowId) return;
      setMatchCount(e.data.count ?? 0);
      setMatchIndex(e.data.index ?? 0);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [windowId]);

  // Push query to all iframes — they'll filter by their own ownership
  function broadcastQuery(q: string, direction: "next" | "prev" | "current" = "current") {
    const iframes = document.querySelectorAll(
      `iframe[data-tab-id]`,
    ) as NodeListOf<HTMLIFrameElement>;
    for (const iframe of Array.from(iframes)) {
      try {
        iframe.contentWindow?.postMessage(
          {
            type: "taos-find:query",
            windowId,
            query: q,
            direction,
          },
          window.location.origin,
        );
      } catch {
        // postMessage throws if the target window is gone; safe to ignore.
      }
    }
  }

  return (
    <div
      role="search"
      aria-label="Find in page"
      className="absolute top-2 right-2 z-50 flex items-center gap-1 rounded-md bg-shell-surface border border-shell-border shadow-lg px-2 py-1"
    >
      <Search size={14} className="opacity-60" aria-hidden="true" />
      <input
        ref={inputRef}
        type="text"
        aria-label="Find query"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          broadcastQuery(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          } else if (e.key === "Enter") {
            e.preventDefault();
            broadcastQuery(query, e.shiftKey ? "prev" : "next");
          }
        }}
        placeholder="Find in page"
        className="w-[200px] bg-transparent text-xs outline-none"
      />
      <span className="text-xs opacity-70 min-w-[60px] text-right">
        {matchCount === null
          ? ""
          : matchCount === 0
          ? "0 matches"
          : `${matchIndex + 1} of ${matchCount}`}
      </span>
      <button
        type="button"
        aria-label="Previous match"
        onClick={() => broadcastQuery(query, "prev")}
        className="p-0.5 rounded hover:bg-shell-hover"
      >
        <ChevronUp size={12} />
      </button>
      <button
        type="button"
        aria-label="Next match"
        onClick={() => broadcastQuery(query, "next")}
        className="p-0.5 rounded hover:bg-shell-hover"
      >
        <ChevronDown size={12} />
      </button>
      <button
        type="button"
        aria-label="Close find"
        onClick={onClose}
        className="p-0.5 rounded hover:bg-shell-hover"
      >
        <X size={12} />
      </button>
    </div>
  );
}
