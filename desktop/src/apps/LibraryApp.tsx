import { useState, useEffect, useCallback, useRef } from "react";
import {
  BookOpen,
  Upload,
  Link,
  RefreshCw,
  Loader2,
  AlertCircle,
  X,
} from "lucide-react";
import { Button, Input } from "@/components/ui";
import { LibraryItemCard } from "@/components/LibraryItemCard";
import { ingestLibraryUrl, type LibraryItem } from "@/lib/library";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Swappable fetch layer with mock fallback                           */
/* ------------------------------------------------------------------ */

const MOCK_ITEMS: LibraryItem[] = [
  {
    id: "mock-1",
    kind: "url:youtube",
    source_url: "https://youtube.com/watch?v=demo1",
    title: "Getting Started with taOS",
    status: "ready",
    storage_path: "",
    bytes: 12 * 1024 * 1024,
    meta_json: JSON.stringify({ preview: "An introduction to taOS features and setup.", duration: 345 }),
    created_at: Date.now() / 1000 - 86400,
    updated_at: Date.now() / 1000 - 3600,
  },
  {
    id: "mock-2",
    kind: "pdf",
    source_url: "",
    title: "Architecture Overview",
    status: "processing",
    storage_path: "",
    bytes: 2 * 1024 * 1024,
    meta_json: JSON.stringify({}),
    created_at: Date.now() / 1000 - 172800,
    updated_at: Date.now() / 1000 - 86400,
  },
  {
    id: "mock-3",
    kind: "text",
    source_url: "",
    title: "Meeting Notes - Q3 Planning",
    status: "ready",
    storage_path: "",
    bytes: 45 * 1024,
    meta_json: JSON.stringify({ preview: "Discussed roadmap priorities and resource allocation for Q3." }),
    created_at: Date.now() / 1000 - 259200,
    updated_at: Date.now() / 1000 - 259200,
  },
];

async function fetchLibraryItems(): Promise<LibraryItem[]> {
  try {
    const res = await fetch("/api/library/items?limit=50", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) throw new Error("Not JSON");
    const data = await res.json();
    if (Array.isArray(data.items)) return data.items;
    return [];
  } catch {
    return MOCK_ITEMS;
  }
}

export function LibraryApp({ windowId: _windowId }: { windowId: string }) {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchLibraryItems();
      setItems(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load library");
      setItems(MOCK_ITEMS);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  /* ---- Drop zone ---- */

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);

    const files = Array.from(e.dataTransfer.files);
    for (const file of files) {
      await ingestFile(file);
    }
  }, []);

  const ingestFile = useCallback(async (file: File) => {
    setIngesting(true);
    try {
      await ingestLibraryUrl(`file://${file.name}`, { title: file.name });
      await loadItems();
    } catch {
      // ignore
    }
    setIngesting(false);
  }, [loadItems]);

  const ingestUrl = useCallback(async (url: string) => {
    setIngesting(true);
    try {
      await ingestLibraryUrl(url, { title: url });
      await loadItems();
    } catch {
      // ignore
    }
    setIngesting(false);
  }, [loadItems]);

  /* ---- Paste ---- */

  useEffect(() => {
    const handler = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData("text");
      if (text && (text.startsWith("http://") || text.startsWith("https://"))) {
        e.preventDefault();
        ingestUrl(text);
      }
    };
    window.addEventListener("paste", handler);
    return () => window.removeEventListener("paste", handler);
  }, [ingestUrl]);

  /* ---- File picker ---- */

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    for (const file of files) {
      await ingestFile(file);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [ingestFile]);

  /* ---- URL form ---- */

  const handleUrlSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (!urlInput.trim()) return;
    ingestUrl(urlInput.trim());
    setUrlInput("");
    setShowUrlInput(false);
  }, [urlInput, ingestUrl]);

  /* ---- Keyboard ---- */

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setShowUrlInput(false);
    }
  }, []);

  const handleItemKeyDown = useCallback((e: React.KeyboardEvent, item: LibraryItem) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (item.source_url) {
        window.open(item.source_url, "_blank");
      }
    }
  }, []);

  return (
    <div
      className={cn(
        "flex flex-col h-full bg-shell-bg-deep select-none",
        dragOver && "ring-2 ring-accent/50 ring-inset",
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="region"
      aria-label="Library drop zone"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-accent" />
          <h1 className="text-sm font-semibold">Library</h1>
          {loading && <Loader2 size={14} className="animate-spin text-shell-text-tertiary" />}
        </div>
        <div className="flex items-center gap-1">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileSelect}
            className="hidden"
            aria-hidden="true"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            aria-label="Choose files to add"
            className="text-xs"
          >
            <Upload size={14} />
            Add files
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowUrlInput((v) => !v)}
            aria-label="Paste URL to add"
            className="text-xs"
            aria-expanded={showUrlInput}
          >
            <Link size={14} />
            Add URL
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={loadItems}
            aria-label="Refresh library"
            className="text-xs"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </div>

      {/* URL input bar */}
      {showUrlInput && (
        <form onSubmit={handleUrlSubmit} className="px-4 py-2 border-b border-white/5 flex items-center gap-2 shrink-0">
          <Input
            type="url"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="Paste or type a URL..."
            className="h-8 text-xs flex-1"
            aria-label="URL to ingest"
            autoFocus
          />
          <Button type="submit" size="sm" disabled={!urlInput.trim() || ingesting} className="text-xs">
            {ingesting ? <Loader2 size={14} className="animate-spin" /> : "Ingest"}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setShowUrlInput(false)} aria-label="Cancel" className="text-xs">
            <X size={14} />
          </Button>
        </form>
      )}

      {/* Main content */}
      <main
        className={cn("flex-1 overflow-y-auto p-4", dragOver && "bg-accent/5")}
        role="list"
        aria-label="Library items"
      >
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary">
            <Loader2 size={24} className="animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
            <BookOpen size={48} className="text-shell-text-tertiary opacity-30" />
            <div>
              <p className="text-sm font-medium text-shell-text-secondary">Your library is empty</p>
              <p className="text-xs text-shell-text-tertiary mt-1">
                Drag files here, paste a URL, or use the buttons above to add items.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {items.map((item) => (
              <div
                key={item.id}
                role="listitem"
                tabIndex={0}
                aria-label={item.title || "Untitled"}
                onKeyDown={(e) => handleItemKeyDown(e, item)}
              >
                <LibraryItemCard
                  item={item}
                  onLinkToCollection={() => {}}
                  onDownload={() => {}}
                  onOpenSource={(it) => {
                    if (it.source_url) window.open(it.source_url, "_blank");
                  }}
                />
              </div>
            ))}
          </div>
        )}
      </main>

      {error && (
        <div className="px-4 py-2 border-t border-red-500/30 bg-red-500/10 text-xs text-red-400 flex items-center gap-2 shrink-0">
          <AlertCircle size={14} />
          {error}
        </div>
      )}
    </div>
  );
}
