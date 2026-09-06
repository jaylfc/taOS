import { useState, useEffect, useCallback, useRef } from "react";
import { Search, Loader2, AlertCircle, ChevronLeft, ChevronRight } from "lucide-react";

interface WallhavenImage {
  id: string;
  url: string;
  path: string;
  thumbs: {
    small: string;
    original: string;
    large: string;
  };
  resolution: string;
  category: string;
  purity: string;
}

interface WallhavenMeta {
  current_page: number;
  last_page: number;
  total: number;
}

interface SearchResponse {
  data: WallhavenImage[];
  meta: WallhavenMeta;
}

interface Props {
  onSelect: (url: string, label: string) => void;
}

type Status = "idle" | "loading" | "error" | "empty" | "results";

export function WallhavenBrowser({ onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [results, setResults] = useState<WallhavenImage[]>([]);
  const [meta, setMeta] = useState<WallhavenMeta | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Debounce search input (300ms)
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Fetch results when debounced query or page changes
  useEffect(() => {
    if (!debouncedQuery.trim()) {
      setResults([]);
      setMeta(null);
      setStatus("idle");
      return;
    }

    let cancelled = false;

    async function fetchResults() {
      setStatus("loading");
      setErrorMsg("");

      try {
        const params = new URLSearchParams({
          q: debouncedQuery,
          page: String(page),
        });
        const resp = await fetch(`/api/wallhaven/search?${params}`);

        if (cancelled) return;

        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          if (cancelled) return;
          setErrorMsg(body.error || `Server error (${resp.status})`);
          setStatus("error");
          return;
        }

        const data: SearchResponse = await resp.json();

        if (cancelled) return;

        setResults(data.data);
        setMeta(data.meta);
        setStatus(data.data.length === 0 ? "empty" : "results");
      } catch {
        if (!cancelled) {
          setErrorMsg("Network error. Check your connection.");
          setStatus("error");
        }
      }
    }

    fetchResults();

    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, page]);

  const handleSelect = useCallback(
    (img: WallhavenImage) => {
      onSelect(img.path, `Wallhaven: ${img.id} (${img.resolution})`);
    },
    [onSelect],
  );

  return (
    <div className="flex flex-col gap-3">
      {/* Search input */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none"
        />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search Wallhaven for wallpapers..."
          aria-label="Search Wallhaven"
          className="w-full pl-7 pr-3 py-1.5 text-xs rounded-md border border-shell-border bg-shell-surface text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/50"
        />
      </div>

      {/* Loading state */}
      {status === "loading" && (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-shell-text-tertiary">
          <Loader2 size={14} className="animate-spin" />
          Searching...
        </div>
      )}

      {/* Error state */}
      {status === "error" && (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-red-400">
          <AlertCircle size={14} />
          {errorMsg}
        </div>
      )}

      {/* Empty state */}
      {status === "empty" && (
        <div className="flex items-center justify-center py-8 text-xs text-shell-text-tertiary">
          No wallpapers found for "{debouncedQuery}"
        </div>
      )}

      {/* Results grid */}
      {status === "results" && (
        <>
          <div className="grid grid-cols-3 gap-2">
            {results.map((img) => (
              <button
                key={img.id}
                onClick={() => handleSelect(img)}
                aria-label={`Set wallpaper: ${img.id} (${img.resolution})`}
                className="relative aspect-video rounded-md overflow-hidden border border-shell-border hover:border-accent/50 transition-colors focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 group"
              >
                <img
                  src={img.thumbs.small}
                  alt={img.id}
                  loading="lazy"
                  className="w-full h-full object-cover"
                />
                <div className="absolute inset-x-0 bottom-0 bg-black/60 px-1.5 py-0.5 text-[10px] text-white/80 opacity-0 group-hover:opacity-100 transition-opacity">
                  {img.resolution}
                </div>
              </button>
            ))}
          </div>

          {/* Pagination */}
          {meta && meta.last_page > 1 && (
            <div className="flex items-center justify-center gap-2 pt-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                aria-label="Previous page"
                className="p-1 rounded text-shell-text-tertiary hover:text-shell-text disabled:opacity-30 disabled:cursor-default"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="text-[10px] tabular-nums text-shell-text-tertiary">
                {meta.current_page} / {meta.last_page}
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= meta.last_page}
                aria-label="Next page"
                className="p-1 rounded text-shell-text-tertiary hover:text-shell-text disabled:opacity-30 disabled:cursor-default"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </>
      )}

      {/* Idle state */}
      {status === "idle" && (
        <div className="flex items-center justify-center py-6 text-xs text-shell-text-tertiary">
          Type a search term to browse Wallhaven wallpapers
        </div>
      )}
    </div>
  );
}
