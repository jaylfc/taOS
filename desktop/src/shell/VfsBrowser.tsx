import { useState, useEffect } from "react";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface VfsEntry {
  name: string;
  type: "file" | "folder";
  size?: number;
  mime_type?: string;
  modified?: string;
}

export interface VfsBrowserProps {
  root: string;        // e.g. "/workspaces/user" or "/workspaces/agent/<slug>"
  onSelect: (path: string | string[]) => void;
  multi?: boolean;     // default false
}

/* ------------------------------------------------------------------ */
/*  Internal — FileEntry shape returned by the API                    */
/* ------------------------------------------------------------------ */

interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
}

/* ------------------------------------------------------------------ */
/*  URL builder                                                        */
/* ------------------------------------------------------------------ */

function buildListUrl(root: string, relativePath: string): string {
  const qs = relativePath ? `?path=${encodeURIComponent(relativePath)}` : "";
  if (root === "/workspaces/user") {
    return `/api/workspace/files${qs}`;
  }
  if (root.startsWith("/workspaces/agent/")) {
    const slug = root.slice("/workspaces/agent/".length);
    return `/api/agents/${encodeURIComponent(slug)}/workspace/files${qs}`;
  }
  // Fallback: treat root as a literal prefix (shouldn't happen in normal use).
  return `/api/workspace/files${qs}`;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export function VfsBrowser({ root, onSelect, multi = false }: VfsBrowserProps) {
  const [currentPath, setCurrentPath] = useState("");
  const [entries, setEntries] = useState<VfsEntry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(buildListUrl(root, currentPath))
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<FileEntry[]>;
      })
      .then((data) => {
        if (cancelled) return;
        const raw = Array.isArray(data) ? data : [];
        const mapped: VfsEntry[] = raw.map((f) => ({
          name: f.name,
          type: f.is_dir ? "folder" : "file",
          size: f.size,
          modified: f.modified ? new Date(f.modified * 1000).toISOString() : undefined,
        }));
        // Folders first, then files, both alphabetically.
        mapped.sort((a, b) => {
          if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
          return a.name.localeCompare(b.name);
        });
        setEntries(mapped);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load");
        setEntries([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [root, currentPath]);

  function absolutePath(name: string): string {
    const rel = currentPath ? `${currentPath}/${name}` : name;
    return `${root}/${rel}`;
  }

  function handleFolderClick(name: string) {
    setCurrentPath((prev) => (prev ? `${prev}/${name}` : name));
    setSelected(new Set());
  }

  function handleFileClick(name: string) {
    const abs = absolutePath(name);
    if (!multi) {
      onSelect(abs);
      return;
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(abs)) {
        next.delete(abs);
      } else {
        next.add(abs);
      }
      onSelect(Array.from(next));
      return next;
    });
  }

  function handleGoUp() {
    setCurrentPath((prev) => {
      const parts = prev.split("/").filter(Boolean);
      parts.pop();
      return parts.join("/");
    });
    setSelected(new Set());
  }

  return (
    <div className="vfs-browser" role="region" aria-label="File browser">
      {currentPath && (
        <button
          onClick={handleGoUp}
          aria-label="Go up one folder"
          style={{ marginBottom: 4 }}
        >
          ↑ ..
        </button>
      )}
      {loading && <p>Loading…</p>}
      {error && <p role="alert">Error: {error}</p>}
      {!loading && !error && entries.length === 0 && <p>Empty folder</p>}
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {entries.map((entry) => {
          const abs = absolutePath(entry.name);
          const isSelected = selected.has(abs);
          return (
            <li key={entry.name}>
              <button
                onClick={() =>
                  entry.type === "folder"
                    ? handleFolderClick(entry.name)
                    : handleFileClick(entry.name)
                }
                aria-selected={multi ? isSelected : undefined}
                style={{ fontWeight: isSelected ? "bold" : undefined }}
              >
                {entry.type === "folder" ? "📁 " : "📄 "}
                {entry.name}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
