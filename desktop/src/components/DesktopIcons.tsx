import { useEffect, useState, useCallback, useRef } from "react";
import { Folder, FileText } from "lucide-react";
import { withCsrf } from "@/lib/csrf";
import { useThemeStore } from "@/stores/theme-store";
import { useProcessStore } from "@/stores/process-store";
import { ContextMenu } from "./ContextMenu";

/**
 * Renders the user's real Desktop folder (workspace/Desktop) as icons on the
 * desktop surface: folders, image thumbnails and file icons. Double-click opens
 * (folder -> Files, file -> stream); right-click gives Open / Rename / Move to
 * Trash; rename is inline. The desktop "New Folder" menu action dispatches a
 * `taos:new-desktop-folder` event which creates an untitled folder here and
 * drops straight into rename (macOS behaviour). Gated by the showDesktopIcons
 * preference.
 */

interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
}

const IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif"]);
const isImg = (n: string) => IMG.has(n.split(".").pop()?.toLowerCase() ?? "");
const DESKTOP = "Desktop";
const fileUrl = (p: string) => `/api/workspace/files/${p.split("/").map(encodeURIComponent).join("/")}`;
// Strip path separators and leading dots so a rename can't escape the Desktop
// folder (the backend also guards, but reject it client-side too).
const cleanName = (raw: string) => raw.trim().replace(/[/\\]/g, "").replace(/^\.+/, "");

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function jmut(url: string, method: string, body?: unknown) {
  const r = await fetch(
    url,
    withCsrf({
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json().catch(() => ({}));
}

export function DesktopIcons() {
  const show = useThemeStore((s) => s.showDesktopIcons);
  const openWindow = useProcessStore((s) => s.openWindow);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number; file: FileEntry } | null>(null);
  const renamingRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const d = await jget<FileEntry[]>(`/api/workspace/files?path=${encodeURIComponent(DESKTOP)}`);
      setFiles(Array.isArray(d) ? d : []);
    } catch {
      setFiles([]);
    }
  }, []);

  useEffect(() => {
    if (!show) return; // don't create / fetch the Desktop dir when icons are hidden
    void (async () => {
      try {
        await jmut("/api/workspace/mkdir", "POST", { path: DESKTOP });
      } catch {
        // best-effort: the dir may already exist
      }
      refresh();
    })();
  }, [refresh, show]);

  const createFolder = useCallback(async () => {
    const names = new Set(files.map((f) => f.name));
    let name = "untitled folder";
    let i = 2;
    while (names.has(name)) name = `untitled folder ${i++}`;
    try {
      await jmut("/api/workspace/mkdir", "POST", { path: `${DESKTOP}/${name}` });
      await refresh();
      setSelected(name);
      setEditing(name);
      setEditName(name);
    } catch (e) {
      console.warn("desktop: create folder failed", e);
    }
  }, [files, refresh]);

  useEffect(() => {
    const h = () => void createFolder();
    window.addEventListener("taos:new-desktop-folder", h);
    return () => window.removeEventListener("taos:new-desktop-folder", h);
  }, [createFolder]);

  const open = useCallback(
    (f: FileEntry) => {
      if (f.is_dir) openWindow("files", { w: 780, h: 540 }, { location: "workspace", path: f.path });
      else window.open(fileUrl(f.path), "_blank");
    },
    [openWindow],
  );

  const commitRename = useCallback(
    async (f: FileEntry) => {
      if (renamingRef.current) return;
      const nn = cleanName(editName);
      if (!nn || nn === f.name) {
        setEditing(null);
        return;
      }
      renamingRef.current = true;
      try {
        await jmut("/api/workspace/rename", "POST", { src: f.path, dst: `${DESKTOP}/${nn}` });
        await refresh();
        setSelected(nn);
      } catch (e) {
        console.warn("desktop: rename failed", e);
      } finally {
        renamingRef.current = false;
        setEditing(null);
      }
    },
    [editName, refresh],
  );

  const del = useCallback(
    async (f: FileEntry) => {
      // Deletion is permanent (no trash yet), so confirm first.
      if (!window.confirm(`Delete "${f.name}"? This can't be undone.`)) return;
      try {
        await jmut(fileUrl(f.path), "DELETE");
        await refresh();
      } catch (e) {
        console.warn("desktop: delete failed", e);
      }
    },
    [refresh],
  );

  if (!show) return null;

  return (
    <>
      <div className="pointer-events-none absolute inset-0 z-0 p-3 pt-4">
        <div className="flex h-full flex-col flex-wrap content-start gap-0.5">
          {files.map((f) => {
            const isEditing = editing === f.name;
            const isSel = selected === f.name;
            return (
              <div
                key={f.path}
                role="button"
                tabIndex={0}
                className={`pointer-events-auto flex w-[84px] cursor-default flex-col items-center gap-1 rounded-lg p-2 text-center ${
                  isSel ? "bg-white/10" : "hover:bg-white/[0.06]"
                }`}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelected(f.name);
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  open(f);
                }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setSelected(f.name);
                  setMenu({ x: e.clientX, y: e.clientY, file: f });
                }}
              >
                <div className="flex h-12 w-12 items-center justify-center">
                  {f.is_dir ? (
                    <Folder size={42} className="text-accent" fill="currentColor" fillOpacity={0.18} />
                  ) : isImg(f.name) ? (
                    <img
                      src={fileUrl(f.path)}
                      alt=""
                      loading="lazy"
                      className="h-11 w-11 rounded border border-white/10 object-cover"
                      onError={(e) => {
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  ) : (
                    <FileText size={38} className="text-shell-text-secondary" />
                  )}
                </div>
                {isEditing ? (
                  <input
                    autoFocus
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    onBlur={() => commitRename(f)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename(f);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    className="w-full rounded border border-accent bg-shell-bg-deep px-1 text-center text-[11px] outline-none"
                  />
                ) : (
                  <span
                    className="line-clamp-2 break-words text-[11px] leading-tight text-shell-text"
                    style={{ textShadow: "0 1px 3px rgba(0,0,0,0.6)" }}
                  >
                    {f.name}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            { label: "Open", action: () => open(menu.file) },
            {
              label: "Rename",
              action: () => {
                setEditing(menu.file.name);
                setEditName(menu.file.name);
              },
            },
            { label: "", separator: true },
            { label: "Delete", action: () => del(menu.file) },
          ]}
        />
      )}
    </>
  );
}
