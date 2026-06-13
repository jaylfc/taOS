import { useState, useMemo, useEffect, useRef } from "react";
import { Search, X } from "lucide-react";
import { getAllApps, getApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import { useShortcut } from "@/hooks/use-shortcut-registry";
import * as icons from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenApp?: (windowId: string) => void;
}

interface SearchResult {
  id: string;
  name: string;
  category: string;
  icon: string;
  type: "app" | "memory" | "action";
  action: () => void;
  subtitle?: string;
}

export function SearchPalette({ open, onClose, onOpenApp }: Props) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const { openWindow } = useProcessStore();
  const [selectedIndex, setSelectedIndex] = useState(0);

  // Register Escape at overlay priority so it beats any system shortcuts when open
  useShortcut("Escape", () => { if (openRef.current) onClose(); }, "Close search", "overlay");

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const appResults = useMemo<SearchResult[]>(() => {
    const all = getAllApps();
    const q = query.toLowerCase().trim();

    return all
      .filter((app) => !q || app.name.toLowerCase().includes(q) || app.category.includes(q))
      .slice(0, 8)
      .map((app) => ({
        id: app.id,
        name: app.name,
        category: app.category,
        icon: app.icon,
        type: "app" as const,
        action: () => {
          const a = getApp(app.id);
          if (a) {
            const wid = openWindow(app.id, a.defaultSize);
            onOpenApp?.(wid);
          }
          onClose();
        },
      }));
  }, [query, openWindow, onClose, onOpenApp]);

  const [memoryResults, setMemoryResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    const q = query.trim();
    if (!q || q.length < 2) {
      setMemoryResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch(
        `/api/user-memory/search?q=${encodeURIComponent(q)}&limit=5`,
        { signal: controller.signal },
      )
        .then((r) => (r.ok ? r.json() : { results: [] }))
        .then((data) => {
          const items = Array.isArray(data?.results) ? data.results : [];
          setMemoryResults(
            items.map((r: { hash: string; title?: string; content: string; collection: string }) => ({
              id: `mem-${r.hash}`,
              name: (r.title && r.title.trim()) || r.content.slice(0, 60),
              category: r.collection,
              icon: "database",
              type: "memory" as const,
              subtitle: r.content.slice(0, 100),
              action: () => {
                const memApp = getApp("memory");
                if (memApp) {
                  const wid = openWindow("memory", memApp.defaultSize);
                  onOpenApp?.(wid);
                }
                onClose();
              },
            })),
          );
        })
        .catch(() => {
          /* ignore (abort or network) */
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, openWindow, onClose, onOpenApp]);

  // Also search session catalog for timeline results
  const [catalogResults, setCatalogResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    const q = query.trim();
    if (!q || q.length < 2) {
      setCatalogResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch(
        `/api/memory/catalog/search?q=${encodeURIComponent(q)}&limit=3`,
        { signal: controller.signal },
      )
        .then((r) => (r.ok ? r.json() : []))
        .then((items) => {
          if (!Array.isArray(items)) return;
          setCatalogResults(
            items.map((s: { id: number; topic: string; description: string; date: string; start_str?: string; end_str?: string; category: string }) => ({
              id: `catalog-${s.id}`,
              name: s.topic || "Session",
              category: s.category || "session",
              icon: "calendar-search",
              type: "memory" as const,
              subtitle: `${s.date || ""} ${s.start_str || ""}-${s.end_str || ""} ${s.description || ""}`.trim().slice(0, 100),
              action: () => {
                const memApp = getApp("memory");
                if (memApp) {
                  const wid = openWindow("memory", memApp.defaultSize);
                  onOpenApp?.(wid);
                }
                onClose();
              },
            })),
          );
        })
        .catch(() => {});
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, openWindow, onClose, onOpenApp]);

  const results = useMemo<SearchResult[]>(
    () => [...appResults, ...memoryResults, ...catalogResults],
    [appResults, memoryResults, catalogResults],
  );

  useEffect(() => {
    setSelectedIndex(0);
  }, [results]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results[selectedIndex]) {
      results[selectedIndex].action();
    }
  };

  if (!open) return null;

  const getIcon = (iconName: string) => {
    const name = iconName
      .split("-")
      .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
      .join("") as keyof typeof icons;
    const Comp = (icons[name] as icons.LucideIcon) ?? icons.HelpCircle;
    return <Comp size={18} />;
  };

  const categoryLabels: Record<string, string> = {
    platform: "Platform",
    os: "Utility",
    streaming: "Streaming",
    game: "Game",
  };

  return (
    <div
      className="fixed inset-0 z-[10003] flex justify-center items-start bg-black/30 backdrop-blur-sm px-4"
      onClick={onClose}
      style={{
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 8px)",
      }}
    >
      <div
        className="w-full max-w-[560px] max-h-[50vh] rounded-xl border border-shell-border-strong overflow-hidden flex flex-col"
        style={{
          backgroundColor: "var(--color-dock-bg)",
          boxShadow: "0 16px 64px rgba(0,0,0,0.5)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-shell-border">
          <Search size={15} className="text-shell-text-tertiary shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search apps, settings, files..."
            className="flex-1 bg-transparent text-sm text-shell-text outline-none placeholder:text-shell-text-tertiary"
            autoFocus
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear">
              <X size={13} className="text-shell-text-tertiary" />
            </button>
          )}
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto py-1">
          {results.length === 0 && query && (
            <div className="px-4 py-8 text-center text-sm text-shell-text-tertiary">
              No results for "{query}"
            </div>
          )}

          {appResults.length > 0 && (
            <div
              className="px-4 pt-2 pb-1 text-[10px] uppercase tracking-wider text-shell-text-tertiary"
              role="presentation"
            >
              Apps
            </div>
          )}
          {appResults.map((result) => {
            const i = results.indexOf(result);
            return (
              <button
                key={result.id}
                onClick={result.action}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${
                  i === selectedIndex ? "bg-accent/15" : "hover:bg-white/5"
                }`}
              >
                <div
                  className={`w-7 h-7 rounded-md flex items-center justify-center ${
                    i === selectedIndex ? "bg-accent/20 text-accent" : "bg-shell-surface text-shell-text-secondary"
                  }`}
                >
                  {getIcon(result.icon)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-shell-text truncate">{result.name}</div>
                </div>
                <span className="text-[9px] text-shell-text-tertiary uppercase tracking-wider">
                  {categoryLabels[result.category] ?? result.category}
                </span>
              </button>
            );
          })}

          {memoryResults.length > 0 && (
            <div
              className="px-4 pt-3 pb-1 text-[10px] uppercase tracking-wider text-shell-text-tertiary"
              role="presentation"
            >
              Memory
            </div>
          )}
          {memoryResults.map((result) => {
            const i = results.indexOf(result);
            return (
              <button
                key={result.id}
                onClick={result.action}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`w-full flex items-start gap-3 px-4 py-2.5 text-left transition-colors ${
                  i === selectedIndex ? "bg-accent/15" : "hover:bg-white/5"
                }`}
              >
                <div
                  className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                    i === selectedIndex
                      ? "bg-slate-500/20 text-slate-300"
                      : "bg-shell-surface text-shell-text-secondary"
                  }`}
                >
                  {getIcon(result.icon)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-shell-text truncate">{result.name}</div>
                  {result.subtitle && (
                    <div className="text-[11px] text-shell-text-tertiary truncate mt-0.5">
                      {result.subtitle}
                    </div>
                  )}
                </div>
                <span className="text-[10px] text-shell-text-tertiary uppercase tracking-wider shrink-0">
                  {result.category}
                </span>
              </button>
            );
          })}

          {!query && (
            <div className="px-4 py-6 text-center text-xs text-shell-text-tertiary">
              Type to search apps and memory, or press Escape to close
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-shell-border text-[10px] text-shell-text-tertiary">
          <span>↑↓ Navigate</span>
          <span>↵ Open</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}
