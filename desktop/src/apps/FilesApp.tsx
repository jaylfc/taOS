import { useState, useEffect, useCallback, useRef } from "react";
import {
  Folder,
  File,
  FileText,
  FileImage,
  FileVideo,
  FileAudio,
  FileCode,
  FileArchive,
  ChevronRight,
  FolderPlus,
  Upload,
  LayoutGrid,
  List,
  Trash2,
  ArrowLeft,
  HardDrive,
  Share2,
  RefreshCw,
  AlertCircle,
  Download,
  Recycle,
  RotateCcw,
  Bot,
  Copy,
  Scissors,
  ClipboardPaste,
  Pencil,
  FolderOpen,
} from "lucide-react";
import { Button, Card, Toolbar, ToolbarGroup, ToolbarSpacer } from "@/components/ui";
import { ContextMenu, type MenuItem } from "@/components/ContextMenu";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { projectsApi, type DocReviewState } from "@/lib/projects";
import { useDragSource } from "@/shell/dnd/use-drag-source";
import { DocViewer } from "@/apps/ProjectsApp/files/DocViewer";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface RecycleItem {
  id: string;
  agent_name: string;
  original_path: string;
  deleted_at: string; // ISO string
}

interface RecycleResponse {
  items: RecycleItem[];
  status?: string;
}

// Item shape returned by GET /api/workspace/trash — the move-to-trash bin
// backing the user's own workspace (see tinyagentos/workspace_trash.py).
// Distinct from RecycleItem above, which is the per-agent container
// trash-cli bin: this one is populated by deletes made in the Files app
// itself, on the user's own workspace files.
interface WorkspaceTrashItem {
  id: string;
  name: string;
  original_path: string;
  is_dir: boolean;
  deleted_at: string; // ISO string
}

interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
}

interface SharedFolder {
  id: number;
  name: string;
  description: string;
  agents: { name: string; permission: string }[];
  created_at?: string;
}

interface AgentSummary {
  name: string;
  display_name?: string;
  emoji?: string;
  framework?: string;
}

interface WorkspaceStats {
  total_files: number;
  total_size: number;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

function formatDate(timestamp: number): string {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

const EXT_ICONS: Record<string, typeof File> = {
  // Images
  png: FileImage, jpg: FileImage, jpeg: FileImage, gif: FileImage, svg: FileImage, webp: FileImage, bmp: FileImage,
  // Video
  mp4: FileVideo, mkv: FileVideo, avi: FileVideo, mov: FileVideo, webm: FileVideo,
  // Audio
  mp3: FileAudio, wav: FileAudio, ogg: FileAudio, flac: FileAudio, aac: FileAudio,
  // Code
  ts: FileCode, tsx: FileCode, js: FileCode, jsx: FileCode, py: FileCode, rs: FileCode, go: FileCode,
  c: FileCode, cpp: FileCode, h: FileCode, java: FileCode, rb: FileCode, sh: FileCode, json: FileCode,
  yaml: FileCode, yml: FileCode, toml: FileCode, xml: FileCode, html: FileCode, css: FileCode,
  // Text
  txt: FileText, md: FileText, log: FileText, csv: FileText, pdf: FileText, doc: FileText, docx: FileText,
  // Archive
  zip: FileArchive, tar: FileArchive, gz: FileArchive, bz2: FileArchive, "7z": FileArchive, rar: FileArchive,
};

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);

function isImage(name: string): boolean {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return IMAGE_EXTS.has(ext);
}

const AGENT_LOCATION_PREFIX = "agent:";

function isAgentLocation(location: string): boolean {
  return location.startsWith(AGENT_LOCATION_PREFIX);
}

function agentSlug(location: string): string {
  return location.slice(AGENT_LOCATION_PREFIX.length);
}

const PROJECT_LOCATION_PREFIX = "project:";

function isProjectLocation(loc: string): boolean {
  return loc.startsWith(PROJECT_LOCATION_PREFIX);
}

function projectSlug(loc: string): string {
  return loc.slice(PROJECT_LOCATION_PREFIX.length);
}

function fileUrl(location: "workspace" | string, path: string): string {
  const encoded = encodeURIComponent(path);
  if (location === "workspace") {
    return `/api/workspace/files/${encoded}`;
  }
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/files/${encoded}`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/files/${encoded}`;
  }
  return `/api/shared-folders/${encodeURIComponent(location)}/files/${encoded}`;
}

/**
 * Build endpoint URLs for the three workspace root kinds:
 *   - "workspace"       → user workspace
 *   - "agent:<slug>"    → per-agent workspace
 *   - "<folder-name>"   → shared folder
 * Centralising the fan-out here keeps fetch/upload/mkdir/delete/watch in sync.
 */
function workspaceListUrl(location: "workspace" | string, path: string): string {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  if (location === "workspace") return `/api/workspace/files${qs}`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/files${qs}`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/files${qs}`;
  }
  return `/api/shared-folders/${encodeURIComponent(location)}/files`;
}

function workspaceWatchUrl(location: "workspace" | string, path: string): string | null {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  if (location === "workspace") return `/api/workspace/files/watch${qs}`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/files/watch${qs}`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/files/watch${qs}`;
  }
  return null;
}

function workspaceUploadUrl(location: "workspace" | string, path: string): string {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  if (location === "workspace") return `/api/workspace/files/upload${qs}`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/files/upload${qs}`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/files/upload${qs}`;
  }
  return `/api/shared-folders/${encodeURIComponent(location)}/upload`;
}

function workspaceMkdirUrl(location: "workspace" | string): string | null {
  if (location === "workspace") return `/api/workspace/mkdir`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/mkdir`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/mkdir`;
  }
  return null;
}

function workspaceDeleteUrl(location: "workspace" | string, path: string): string | null {
  const encoded = encodeURIComponent(path);
  if (location === "workspace") return `/api/workspace/files/${encoded}`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/files/${encoded}`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/files/${encoded}`;
  }
  return null;
}

function workspaceCopyUrl(location: "workspace" | string): string | null {
  // Only the user workspace exposes a copy endpoint on the backend. Agent /
  // project / shared-folder locations do not, so copy/cut/paste stays disabled
  // there to avoid runtime 404s.
  if (location === "workspace") return `/api/workspace/copy`;
  return null;
}

function workspaceRenameUrl(location: "workspace" | string): string | null {
  // Only the user workspace exposes a rename endpoint on the backend.
  if (location === "workspace") return `/api/workspace/rename`;
  return null;
}

function workspaceStatsUrl(location: "workspace" | string): string | null {
  if (location === "workspace") return `/api/workspace/stats`;
  if (isAgentLocation(location)) {
    return `/api/agents/${encodeURIComponent(agentSlug(location))}/workspace/stats`;
  }
  if (isProjectLocation(location)) {
    return `/api/projects/${encodeURIComponent(projectSlug(location))}/stats`;
  }
  return null;
}

// Split a file name into its base and extension. Directories (and dotfiles
// with no extension) keep the whole name as the base so the " copy" suffix
// lands before any real extension.
function splitNameExt(name: string): { base: string; ext: string } {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return { base: name, ext: "" };
  return { base: name.slice(0, dot), ext: name.slice(dot) };
}

// Pick a name that does not collide with anything already in the listing.
// Produces "foo copy.txt", then "foo copy 2.txt", "foo copy 3.txt", ...
function nextCopyName(name: string, taken: Set<string>): string {
  const { base, ext } = splitNameExt(name);
  let candidate = `${base} copy${ext}`;
  let n = 2;
  while (taken.has(candidate)) {
    candidate = `${base} copy ${n}${ext}`;
    n += 1;
  }
  return candidate;
}

function getFileIcon(name: string, isDir: boolean) {
  if (isDir) return Folder;
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_ICONS[ext] ?? File;
}

const REVIEW_BADGE_STYLES: Record<string, string> = {
  approved: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  changes_requested: "bg-amber-500/15 text-amber-400 border-amber-500/20",
  awaiting_review: "bg-zinc-500/10 text-zinc-400 border-zinc-500/15",
};

const REVIEW_BADGE_LABEL: Record<string, string> = {
  approved: "Approved",
  changes_requested: "Changes requested",
  awaiting_review: "Awaiting review",
};

function ReviewBadge({
  state,
  onClick,
}: {
  state: string | null;
  onClick?: () => void;
}) {
  if (!state) return null;
  const style = REVIEW_BADGE_STYLES[state] ?? REVIEW_BADGE_STYLES.awaiting_review;
  const label = REVIEW_BADGE_LABEL[state] ?? state;
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={`Review state: ${label} (click to change)`}
        className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full border ${style} hover:brightness-125 transition`}
      >
        {label}
      </button>
    );
  }
  return (
    <span
      className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full border ${style}`}
      title={`Review state: ${label}`}
    >
      {label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  API helpers                                                        */
/* ------------------------------------------------------------------ */

async function apiFetch<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(url, opts);
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("text/html")) {
    throw new Error("API returned HTML — endpoint may not support JSON mode");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body || `HTTP ${res.status}`);
  }
  return res.json();
}

/* ------------------------------------------------------------------ */
/*  FileRow — list-view row with drag source                          */
/* ------------------------------------------------------------------ */

interface FileRowProps {
  f: FileEntry;
  location: "workspace" | string;
  currentPath: string;
  navigateTo: (path: string) => void;
  isWritable: boolean;
  deleteConfirm: string | null;
  handleDelete: (path: string) => void;
  setDeleteConfirm: (path: string | null) => void;
  onContextMenu: (e: React.MouseEvent, f: FileEntry) => void;
  isCut: boolean;
  reviewState: string | null;
  onDocReviewClick?: () => void;
}

function FileRow({
  f,
  location,
  currentPath,
  navigateTo,
  isWritable,
  deleteConfirm,
  handleDelete,
  setDeleteConfirm,
  onContextMenu,
  isCut,
  reviewState,
  onDocReviewClick,
}: FileRowProps) {
  const Icon = getFileIcon(f.name, f.is_dir);
  const relPath = f.path || (currentPath ? `${currentPath}/${f.name}` : f.name);

  let vfsPath: string | null = null;
  if (location === "workspace") {
    vfsPath = `/workspaces/user/${relPath}`;
  } else if (isAgentLocation(location)) {
    vfsPath = `/workspaces/agent/${agentSlug(location)}/${relPath}`;
  }

  const dragEnabled = !!vfsPath && !f.is_dir;
  const { dragHandlers } = useDragSource({
    // When dragEnabled is false, `disabled: true` short-circuits the payload
    // before it ever lands on the bus — the empty-string placeholder below
    // is never read.
    payload: {
      kind: "file",
      path: vfsPath ?? "",
      mime_type: "application/octet-stream",
      size: f.size ?? 0,
      name: f.name,
    },
    disabled: !dragEnabled,
    htmlMirror: dragEnabled && vfsPath ? { "text/plain": vfsPath } : undefined,
  });

  return (
    <tr
      key={f.path || f.name}
      data-file-row
      onContextMenu={(e) => onContextMenu(e, f)}
      className={`border-b border-white/5 hover:bg-shell-surface/50 transition-colors group ${isCut ? "opacity-50" : ""}`}
      {...dragHandlers}
    >
      <td className="px-3 py-2">
        <button
          onClick={() => {
            if (f.is_dir) {
              navigateTo(f.path || (currentPath ? `${currentPath}/${f.name}` : f.name));
            }
          }}
          className="flex items-center gap-2 min-w-0"
          aria-label={f.is_dir ? `Open folder ${f.name}` : `File ${f.name}`}
        >
          {!f.is_dir && isImage(f.name) ? (
            <img
              src={fileUrl(location, f.path || f.name)}
              alt=""
              loading="lazy"
              decoding="async"
              className="w-6 h-6 rounded object-cover border border-white/[0.06] shrink-0"
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
          ) : (
            <Icon size={16} className={f.is_dir ? "text-accent shrink-0" : "text-shell-text-secondary shrink-0"} />
          )}
          <span className="truncate">{f.name}</span>
        </button>
      </td>
      <td className="px-3 py-2 text-shell-text-tertiary">
        {f.is_dir ? "—" : formatSize(f.size)}
      </td>
      <td className="px-3 py-2 text-shell-text-tertiary">
        {formatDate(f.modified)}
      </td>
      <td className="px-3 py-2">
        {!f.is_dir && isProjectLocation(location) && (
          <ReviewBadge state={reviewState} onClick={onDocReviewClick} />
        )}
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-1">
          {!f.is_dir && isWritable && (
            <a
              href={fileUrl(location, f.path || f.name)}
              target="_blank"
              rel="noopener noreferrer"
              className="p-1 rounded-md opacity-0 group-hover:opacity-100 hover:bg-shell-surface transition-all text-shell-text-tertiary hover:text-shell-text"
              aria-label={`Download ${f.name}`}
            >
              <Download size={13} />
            </a>
          )}
          {isWritable && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                if (deleteConfirm === f.path) {
                  handleDelete(f.path);
                } else {
                  setDeleteConfirm(f.path);
                }
              }}
              className={`h-7 w-7 transition-all ${
                deleteConfirm === f.path
                  ? "bg-red-500/20 text-red-400 opacity-100 hover:bg-red-500/25 hover:text-red-400"
                  : "opacity-0 group-hover:opacity-100 hover:bg-red-500/20 hover:text-red-400"
              }`}
              aria-label={deleteConfirm === f.path ? `Confirm delete ${f.name}` : `Delete ${f.name}`}
              title={deleteConfirm === f.path ? "Click again to confirm" : "Delete"}
            >
              <Trash2 size={13} />
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function FilesApp({
  windowId: _windowId,
  rootPath,
  path: initialPath,
}: { windowId: string; rootPath?: string; path?: string }) {
  const isMobile = useIsMobile();

  // `path` opens straight into a sub-directory (e.g. double-clicking a Desktop
  // folder); the load effect fetches currentPath on mount.
  const [currentPath, setCurrentPath] = useState(initialPath ?? "");
  const [location, setLocation] = useState<"workspace" | string>(() => rootPath ?? "workspace");
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [sharedFolders, setSharedFolders] = useState<SharedFolder[]>([]);
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [agentsExpanded, setAgentsExpanded] = useState(true);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<WorkspaceStats | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sharedExpanded, setSharedExpanded] = useState(true);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  // null = showing sidebar (list pane); non-null = showing file browser (detail pane).
  // An initial path (or rootPath) opens straight into the browser pane.
  const [selectedLocation, setSelectedLocation] = useState<string | null>(
    rootPath ?? (initialPath != null ? "workspace" : null),
  );

  // Clipboard for cut/copy/paste. Cross-location paste is out of scope for v1.
  const [clipboard, setClipboard] = useState<
    { mode: "cut" | "copy"; location: string; path: string; name: string } | null
  >(null);
  // Context menu anchor + payload. file === null targets the empty list area.
  const [menu, setMenu] = useState<{ x: number; y: number; file: FileEntry | null } | null>(null);

  // In-app markdown reader: set when a .md/.markdown file is opened from a
  // project: location, replacing the window.open download behaviour. Cleared
  // by DocViewer's Close affordance.
  const [docViewer, setDocViewer] = useState<{ url: string; title: string } | null>(null);

  // Recycle bin (per-agent container trash-cli)
  const [recycleItems, setRecycleItems] = useState<RecycleItem[]>([]);
  const [recycleContainerOffline, setRecycleContainerOffline] = useState<string[]>([]);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [recycleError, setRecycleError] = useState<string | null>(null);

  // Workspace trash (host-side move-to-trash backing the user's own Files)
  const [workspaceTrashItems, setWorkspaceTrashItems] = useState<WorkspaceTrashItem[]>([]);
  const [workspaceTrashLoading, setWorkspaceTrashLoading] = useState(false);
  const [workspaceTrashError, setWorkspaceTrashError] = useState<string | null>(null);

  // Doc-review stamp state (project: locations only)
  const [reviewStates, setReviewStates] = useState<Record<string, string | null>>({});

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounter = useRef(0);

  const fetchReviewStates = useCallback(async (loc: string) => {
    if (!isProjectLocation(loc)) {
      setReviewStates({});
      return;
    }
    const pid = projectSlug(loc);
    try {
      const items = await projectsApi.docReviews.list(pid);
      const map: Record<string, string | null> = {};
      for (const item of items) {
        if (item.review_state) map[item.doc_path] = item.review_state;
      }
      setReviewStates(map);
    } catch {
      setReviewStates({});
    }
  }, []);

  const cycleDocReview = useCallback(
    async (docPath: string, current: string | null) => {
      if (!isProjectLocation(location)) return;
      const pid = projectSlug(location);
      const order: DocReviewState[] = ["awaiting_review", "approved", "changes_requested"];
      const idx = current ? order.indexOf(current as DocReviewState) : 0;
      const next = order[(idx + 1) % order.length]!;
      try {
        await projectsApi.docReviews.set(pid, docPath, next);
        setReviewStates((prev) => {
          const nextMap: Record<string, string | null> = { ...prev, [docPath]: next };
          return nextMap;
        });
      } catch {
        /* write rejected (forbidden / offline): keep the current stamp */
      }
    },
    [location],
  );

  // Workspace locations (user + per-agent + project) allow mutations and the stats
  // endpoint. Shared folders and the recycle bin are read-only here.
  const isWritable = location === "workspace" || isAgentLocation(location) || isProjectLocation(location);
  const locationTitle =
    location === "recycle"
      ? "Recycle Bin"
      : location === "workspace"
        ? "Workspace"
        : isAgentLocation(location)
          ? agents?.find((a) => a.name === agentSlug(location))?.display_name ?? agentSlug(location)
          : isProjectLocation(location)
            ? projectSlug(location)
            : location;

  /* ---- Fetch files ---- */
  const fetchFiles = useCallback(async (path = "") => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<FileEntry[]>(workspaceListUrl(location, path));
      if (location === "workspace" || isAgentLocation(location)) {
        setFiles(Array.isArray(data) ? data : []);
      } else {
        // Shared folders return a shallower shape; normalise for the UI.
        setFiles(Array.isArray(data) ? data.map((f) => ({ ...f, is_dir: f.is_dir ?? false, path: f.path ?? f.name })) : []);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load files";
      setError(msg);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [location]);

  const navigateTo = useCallback((path: string) => {
    setCurrentPath(path);
    setDeleteConfirm(null);
  }, []);

  const goUp = useCallback(() => {
    if (!currentPath) return;
    const parts = currentPath.split("/").filter(Boolean);
    parts.pop();
    navigateTo(parts.join("/"));
  }, [currentPath, navigateTo]);

  /* ---- Effects ---- */
  useEffect(() => {
    fetchFiles(currentPath);
  }, [currentPath, fetchFiles]);

  // Live updates via SSE — supports both the user workspace and per-agent
  // workspaces. Shared folders don't expose a watch stream, so the SSE setup
  // is skipped for them (workspaceWatchUrl returns null).
  useEffect(() => {
    const url = workspaceWatchUrl(location, currentPath);
    if (!url) return;
    let eventSource: EventSource | null = null;
    try {
      eventSource = new EventSource(url);
    } catch {
      return;
    }
    const es = eventSource;
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (Array.isArray(data)) {
          setFiles(data);
        }
      } catch {
        // Ignore malformed events
      }
    };
    es.onerror = () => {
      // Silently let the browser retry
    };
    return () => {
      es.close();
    };
  }, [currentPath, location]);

  useEffect(() => {
    apiFetch<SharedFolder[]>("/api/shared-folders")
      .then((d) => setSharedFolders(Array.isArray(d) ? d : []))
      .catch(() => setSharedFolders([]));
  }, []);

  useEffect(() => {
    apiFetch<AgentSummary[]>("/api/agents")
      .then((d) => setAgents(Array.isArray(d) ? d : []))
      .catch(() => setAgents([]));
  }, []);

  useEffect(() => {
    const url = workspaceStatsUrl(location);
    if (!url) {
      setStats(null);
      return;
    }
    apiFetch<WorkspaceStats>(url)
      .then(setStats)
      .catch(() => setStats(null));
  }, [location]);

  // Refresh doc-review badges whenever we switch project locations.
  useEffect(() => {
    fetchReviewStates(location);
  }, [location, fetchReviewStates]);

  /* ---- Recycle bin ---- */
  const fetchRecycle = useCallback(async () => {
    setRecycleLoading(true);
    setRecycleError(null);
    try {
      const res = await fetch("/api/recycle", { headers: { Accept: "application/json" } });
      if (!res.ok) {
        setRecycleError(`Failed to load recycle bin (${res.status})`);
        return;
      }
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) {
        setRecycleError("Unexpected response from server");
        return;
      }
      const data: RecycleResponse = await res.json();
      const items = Array.isArray(data.items) ? data.items : [];
      setRecycleItems(items);
      // Detect any per-agent offline flags (items marked with container_offline status)
      const offlineAgents: string[] = [];
      if (data.status === "container_offline") {
        // top-level offline; all agents offline
      }
      setRecycleContainerOffline(offlineAgents);
    } catch (e: unknown) {
      setRecycleError(e instanceof Error ? e.message : "Failed to load recycle bin");
    } finally {
      setRecycleLoading(false);
    }
  }, []);

  const handleRecycleRestore = useCallback(async (item: RecycleItem) => {
    if (!window.confirm(`Restore \`${item.original_path}\` to its original location?`)) return;
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(item.agent_name)}/recycle/restore`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ id: item.id }),
        }
      );
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        setRecycleError(`Restore failed: ${body || res.status}`);
        return;
      }
      fetchRecycle();
    } catch (e: unknown) {
      setRecycleError(e instanceof Error ? e.message : "Restore failed");
    }
  }, [fetchRecycle]);

  const handleRecycleDelete = useCallback(async (item: RecycleItem) => {
    if (!window.confirm(`Permanently delete? This cannot be undone.`)) return;
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(item.agent_name)}/recycle/${encodeURIComponent(item.id)}`,
        { method: "DELETE", headers: { Accept: "application/json" } }
      );
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        setRecycleError(`Delete failed: ${body || res.status}`);
        return;
      }
      fetchRecycle();
    } catch (e: unknown) {
      setRecycleError(e instanceof Error ? e.message : "Delete failed");
    }
  }, [fetchRecycle]);

  /* ---- Workspace trash ---- */
  const fetchWorkspaceTrash = useCallback(async () => {
    setWorkspaceTrashLoading(true);
    setWorkspaceTrashError(null);
    try {
      const data = await apiFetch<{ items: WorkspaceTrashItem[] }>("/api/workspace/trash");
      setWorkspaceTrashItems(Array.isArray(data.items) ? data.items : []);
    } catch (e: unknown) {
      setWorkspaceTrashError(e instanceof Error ? e.message : "Failed to load trash");
    } finally {
      setWorkspaceTrashLoading(false);
    }
  }, []);

  const handleWorkspaceTrashRestore = useCallback(async (item: WorkspaceTrashItem) => {
    try {
      await apiFetch(`/api/workspace/trash/${encodeURIComponent(item.id)}/restore`, { method: "POST" });
      fetchWorkspaceTrash();
    } catch (e: unknown) {
      setWorkspaceTrashError(e instanceof Error ? e.message : "Restore failed");
    }
  }, [fetchWorkspaceTrash]);

  const handleWorkspaceTrashPurge = useCallback(async (item: WorkspaceTrashItem) => {
    if (!window.confirm(`Permanently delete "${item.original_path}"? This cannot be undone.`)) return;
    try {
      await apiFetch(`/api/workspace/trash/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      fetchWorkspaceTrash();
    } catch (e: unknown) {
      setWorkspaceTrashError(e instanceof Error ? e.message : "Delete failed");
    }
  }, [fetchWorkspaceTrash]);

  const handleEmptyWorkspaceTrash = useCallback(async () => {
    if (workspaceTrashItems.length === 0) return;
    if (!window.confirm("Permanently delete everything in the trash? This cannot be undone.")) return;
    try {
      await apiFetch("/api/workspace/trash", { method: "DELETE" });
      fetchWorkspaceTrash();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Empty trash failed";
      setError(msg);
    }
  }, [workspaceTrashItems.length, fetchWorkspaceTrash]);

  /* ---- Actions ---- */
  const handleNewFolder = useCallback(async () => {
    const mkdirUrl = workspaceMkdirUrl(location);
    if (!mkdirUrl) return;
    const name = prompt("New folder name:");
    if (!name?.trim()) return;
    const fullPath = currentPath ? `${currentPath}/${name.trim()}` : name.trim();
    try {
      await apiFetch(mkdirUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: fullPath }),
      });
      fetchFiles(currentPath);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to create folder";
      setError(msg);
    }
  }, [currentPath, fetchFiles, location]);

  const handleUpload = useCallback(async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const uploadUrl = workspaceUploadUrl(location, currentPath);
      for (let i = 0; i < fileList.length; i++) {
        const file = fileList.item(i);
        if (!file) continue;
        const form = new FormData();
        form.append("file", file);
        await apiFetch(uploadUrl, { method: "POST", body: form });
      }
      fetchFiles(currentPath);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      setError(msg);
    } finally {
      setUploading(false);
    }
  }, [currentPath, location, fetchFiles]);

  const handleDelete = useCallback(async (filePath: string) => {
    const delUrl = workspaceDeleteUrl(location, filePath);
    if (!delUrl) return;
    try {
      await apiFetch(delUrl, { method: "DELETE" });
      setDeleteConfirm(null);
      fetchFiles(currentPath);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      setError(msg);
    }
  }, [currentPath, fetchFiles, location]);

  const handleRename = useCallback(async (file: FileEntry) => {
    const renameUrl = workspaceRenameUrl(location);
    if (!renameUrl) return;
    const next = prompt("Rename to:", file.name);
    const trimmed = next?.trim();
    if (!trimmed || trimmed === file.name) return;
    const dst = currentPath ? `${currentPath}/${trimmed}` : trimmed;
    try {
      await apiFetch(renameUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src: file.path, dst }),
      });
      fetchFiles(currentPath);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Rename failed");
    }
  }, [currentPath, fetchFiles, location]);

  const handlePaste = useCallback(async () => {
    if (!clipboard) return;
    // Cross-location paste is not supported in v1.
    if (clipboard.location !== location) return;
    const url = clipboard.mode === "copy" ? workspaceCopyUrl(location) : workspaceRenameUrl(location);
    if (!url) return;

    let name = clipboard.name;
    let dst = currentPath ? `${currentPath}/${name}` : name;

    if (dst === clipboard.path) {
      // Pasting into the same directory the item came from.
      if (clipboard.mode === "cut") {
        // Moving onto itself is a no-op.
        setClipboard(null);
        return;
      }
      // Copy: duplicate in place under a non-colliding name.
      const taken = new Set(files.map((f) => f.name));
      name = nextCopyName(clipboard.name, taken);
      dst = currentPath ? `${currentPath}/${name}` : name;
    }

    try {
      await apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src: clipboard.path, dst }),
      });
      if (clipboard.mode === "cut") setClipboard(null);
      fetchFiles(currentPath);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Paste failed");
    }
  }, [clipboard, currentPath, fetchFiles, files, location]);

  /* ---- Drag and drop ---- */
  const onDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current++;
    setDragging(true);
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current--;
    if (dragCounter.current <= 0) {
      dragCounter.current = 0;
      setDragging(false);
    }
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current = 0;
    setDragging(false);
    handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  /* ---- Breadcrumbs ---- */
  const pathSegments = currentPath ? currentPath.split("/").filter(Boolean) : [];

  /* ---- Sort: folders first, then alphabetical ---- */
  const sortedFiles = [...files].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  /* ---- Sidebar (list pane) ---- */
  const sidebarUI = (
    <div className="flex flex-col h-full bg-shell-bg-deep">
      {!isMobile && (
        <div className="px-3 pt-3 pb-2 text-xs font-semibold text-shell-text-tertiary uppercase tracking-wider">
          Locations
        </div>
      )}

      {/* iOS 26 grouped list on mobile */}
      <div className={isMobile ? "px-4 pt-4 space-y-3" : "space-y-0.5"}>
        {/* Workspace row */}
        {isMobile ? (
          <div
            style={{ background: "rgba(255,255,255,0.05)", borderRadius: 16 }}
            className="overflow-hidden"
          >
            <button
              onClick={() => {
                setLocation("workspace");
                setCurrentPath("");
                setSelectedLocation("workspace");
              }}
              className="w-full flex items-center justify-between px-4 py-3.5 text-sm text-shell-text active:bg-white/10 transition-colors"
              aria-label="My Workspace"
            >
              <span className="flex items-center gap-3">
                <HardDrive size={16} className="text-accent" />
                <span>My Workspace</span>
              </span>
              <ChevronRight size={16} className="text-shell-text-tertiary" />
            </button>
          </div>
        ) : (
          <Button
            variant={location === "workspace" ? "secondary" : "ghost"}
            onClick={() => { setLocation("workspace"); setCurrentPath(""); }}
            className="w-full justify-start mx-1.5 px-3"
            aria-label="My Workspace"
          >
            <HardDrive size={16} />
            <span className="truncate">My Workspace</span>
          </Button>
        )}

        {/* Shared Folders */}
        {isMobile ? (
          sharedFolders.length > 0 && (
            <div>
              <div className="px-1 pb-1 text-xs font-semibold text-shell-text-tertiary uppercase tracking-wider">
                Shared Folders
              </div>
              <div
                style={{ background: "rgba(255,255,255,0.05)", borderRadius: 16 }}
                className="overflow-hidden divide-y divide-white/[0.04]"
              >
                {sharedFolders.map((sf) => (
                  <button
                    key={sf.id}
                    onClick={() => {
                      setLocation(sf.name);
                      setCurrentPath("");
                      setSelectedLocation(sf.name);
                    }}
                    className="w-full flex items-center justify-between px-4 py-3.5 text-sm text-shell-text active:bg-white/10 transition-colors"
                    aria-label={`Shared folder: ${sf.name}`}
                    title={sf.description}
                  >
                    <span className="flex items-center gap-3">
                      <Folder size={16} className="text-accent" />
                      <span className="truncate">{sf.name}</span>
                    </span>
                    <ChevronRight size={16} className="text-shell-text-tertiary" />
                  </button>
                ))}
              </div>
            </div>
          )
        ) : (
          <>
            <Button
              variant="ghost"
              onClick={() => setSharedExpanded(!sharedExpanded)}
              className="w-full justify-start mx-1.5 mt-1 px-3"
              aria-label="Toggle shared folders"
            >
              <Share2 size={16} />
              <span className="flex-1 truncate text-left">Shared Folders</span>
              <ChevronRight size={14} className={`transition-transform ${sharedExpanded ? "rotate-90" : ""}`} />
            </Button>

            {sharedExpanded && (
              <div className="ml-5 mr-1.5">
                {sharedFolders.length === 0 && (
                  <div className="px-3 py-2 text-xs text-shell-text-tertiary">No shared folders</div>
                )}
                {sharedFolders.map((sf) => (
                  <Button
                    key={sf.id}
                    variant={location === sf.name ? "secondary" : "ghost"}
                    onClick={() => { setLocation(sf.name); setCurrentPath(""); }}
                    className="w-full justify-start px-3 py-1.5 h-auto text-xs font-normal"
                    aria-label={`Shared folder: ${sf.name}`}
                    title={sf.description}
                  >
                    <Folder size={14} />
                    <span className="truncate">{sf.name}</span>
                  </Button>
                ))}
              </div>
            )}
          </>
        )}

        {/* Agents — per-agent workspace browser. Loading state shows a small
            skeleton; empty state hides the group entirely so the sidebar
            stays tidy when nothing is deployed. */}
        {isMobile ? (
          agents === null ? (
            <div className="h-10 rounded-2xl bg-white/[0.04] animate-pulse" aria-hidden="true" />
          ) : agents.length > 0 ? (
            <div>
              <div className="px-1 pb-1 text-xs font-semibold text-shell-text-tertiary uppercase tracking-wider">
                Agents
              </div>
              <div
                style={{ background: "rgba(255,255,255,0.05)", borderRadius: 16 }}
                className="overflow-hidden divide-y divide-white/[0.04]"
              >
                {agents.map((a) => {
                  const locKey = `${AGENT_LOCATION_PREFIX}${a.name}`;
                  const label = a.display_name || a.name;
                  return (
                    <button
                      key={a.name}
                      onClick={() => {
                        setLocation(locKey);
                        setCurrentPath("");
                        setSelectedLocation(locKey);
                      }}
                      className="w-full flex items-center justify-between px-4 py-3.5 text-sm text-shell-text active:bg-white/10 transition-colors"
                      aria-label={`Agent workspace: ${label}`}
                    >
                      <span className="flex items-center gap-3 min-w-0">
                        <span className="text-base leading-none shrink-0" aria-hidden="true">
                          {resolveAgentEmoji(a.emoji, a.framework)}
                        </span>
                        <span className="truncate">{label}</span>
                      </span>
                      <ChevronRight size={16} className="text-shell-text-tertiary" />
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null
        ) : (
          agents === null ? (
            <div className="mx-1.5 mt-1 h-8 rounded-md bg-white/[0.04] animate-pulse" aria-hidden="true" />
          ) : agents.length > 0 ? (
            <>
              <Button
                variant="ghost"
                onClick={() => setAgentsExpanded(!agentsExpanded)}
                className="w-full justify-start mx-1.5 mt-1 px-3"
                aria-label="Toggle agents"
              >
                <Bot size={16} />
                <span className="flex-1 truncate text-left">Agents</span>
                <ChevronRight size={14} className={`transition-transform ${agentsExpanded ? "rotate-90" : ""}`} />
              </Button>

              {agentsExpanded && (
                <div className="ml-5 mr-1.5">
                  {agents.map((a) => {
                    const locKey = `${AGENT_LOCATION_PREFIX}${a.name}`;
                    const label = a.display_name || a.name;
                    return (
                      <Button
                        key={a.name}
                        variant={location === locKey ? "secondary" : "ghost"}
                        onClick={() => { setLocation(locKey); setCurrentPath(""); }}
                        className="w-full justify-start px-3 py-1.5 h-auto text-xs font-normal"
                        aria-label={`Agent workspace: ${label}`}
                      >
                        <span className="text-sm leading-none" aria-hidden="true">
                          {resolveAgentEmoji(a.emoji, a.framework)}
                        </span>
                        <span className="truncate">{label}</span>
                      </Button>
                    );
                  })}
                </div>
              )}
            </>
          ) : null
        )}

        {/* Recycle Bin */}
        {isMobile ? (
          <div
            style={{ background: "rgba(255,255,255,0.05)", borderRadius: 16 }}
            className="overflow-hidden"
          >
            <button
              onClick={() => {
                setLocation("recycle");
                setCurrentPath("");
                setSelectedLocation("recycle");
                fetchRecycle();
                fetchWorkspaceTrash();
              }}
              className={`w-full flex items-center justify-between px-4 py-3.5 text-sm text-shell-text active:bg-white/10 transition-colors ${location === "recycle" ? "bg-white/10" : ""}`}
              aria-label="Recycle Bin"
            >
              <span className="flex items-center gap-3">
                <Recycle size={16} className="text-shell-text-secondary" aria-hidden="true" />
                <span>Recycle Bin</span>
              </span>
              <ChevronRight size={16} className="text-shell-text-tertiary" />
            </button>
          </div>
        ) : (
          <Button
            variant={location === "recycle" ? "secondary" : "ghost"}
            onClick={() => {
              setLocation("recycle");
              setCurrentPath("");
              fetchRecycle();
              fetchWorkspaceTrash();
            }}
            className="w-full justify-start mx-1.5 mt-1 px-3"
            aria-label="Recycle Bin"
          >
            <Recycle size={16} aria-hidden="true" />
            <span className="truncate">Recycle Bin</span>
          </Button>
        )}
      </div>

      <div className="flex-1" />

      {/* Stats */}
      {stats && isWritable && (
        <div className="px-3 py-3 border-t border-white/5 text-xs text-shell-text-tertiary space-y-1">
          <div>{stats.total_files} files</div>
          <div>{formatSize(stats.total_size)} used</div>
        </div>
      )}
    </div>
  );

  /* ---- Toolbar actions (shared between mobile and desktop) ---- */
  const toolbarActions = (
    <>
      {isWritable && (
        <Button
          variant="ghost"
          size="icon"
          onClick={handleNewFolder}
          className="h-8 w-8"
          aria-label="New folder"
          title="New folder"
        >
          <FolderPlus size={14} />
        </Button>
      )}

      <Button
        variant="default"
        size="sm"
        onClick={() => fileInputRef.current?.click()}
        aria-label="Upload file"
      >
        <Upload size={14} />
        <span className="hidden sm:inline">Upload</span>
      </Button>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => handleUpload(e.target.files)}
        aria-label="File upload input"
      />

      <Button
        variant="ghost"
        size="icon"
        onClick={() => fetchFiles(currentPath)}
        className="h-8 w-8"
        aria-label="Refresh file list"
      >
        <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
      </Button>

      <div className="flex items-center rounded-lg bg-shell-surface overflow-hidden">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setViewMode("grid")}
          className={`h-8 w-8 rounded-none ${viewMode === "grid" ? "bg-accent/20 text-accent hover:bg-accent/25" : ""}`}
          aria-label="Grid view"
        >
          <LayoutGrid size={14} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setViewMode("list")}
          className={`h-8 w-8 rounded-none ${viewMode === "list" ? "bg-accent/20 text-accent hover:bg-accent/25" : ""}`}
          aria-label="List view"
        >
          <List size={14} />
        </Button>
      </div>
    </>
  );

  /* ---- Recycle bin UI ---- */
  // Group items by agent name, sorted newest first within each group
  const recycleByAgent: Record<string, RecycleItem[]> = {};
  for (const item of recycleItems) {
    if (!recycleByAgent[item.agent_name]) recycleByAgent[item.agent_name] = [];
    recycleByAgent[item.agent_name]!.push(item);
  }
  // Sort each agent's items newest first
  for (const key of Object.keys(recycleByAgent)) {
    recycleByAgent[key]!.sort(
      (a, b) => new Date(b.deleted_at).getTime() - new Date(a.deleted_at).getTime()
    );
  }

  const recycleBinUI = (
    <div className="w-full h-full flex flex-col min-w-0">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-white/5">
        <Recycle size={15} className="text-shell-text-tertiary" aria-hidden="true" />
        <span className="text-sm font-medium">Recycle Bin</span>
        {workspaceTrashItems.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleEmptyWorkspaceTrash}
            className="h-7 ml-auto text-xs text-shell-text-tertiary hover:text-red-400"
            aria-label="Empty workspace trash"
          >
            Empty Trash
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            fetchRecycle();
            fetchWorkspaceTrash();
          }}
          className={`h-7 w-7 ${workspaceTrashItems.length > 0 ? "" : "ml-auto"}`}
          aria-label="Refresh recycle bin"
        >
          <RefreshCw size={13} className={recycleLoading || workspaceTrashLoading ? "animate-spin" : ""} aria-hidden="true" />
        </Button>
      </div>

      {/* Error */}
      {(recycleError || workspaceTrashError) && (
        <div className="mx-3 mt-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs">
          <AlertCircle size={14} className="shrink-0" aria-hidden="true" />
          <span className="flex-1">{recycleError || workspaceTrashError}</span>
          <button
            onClick={() => {
              setRecycleError(null);
              setWorkspaceTrashError(null);
            }}
            className="hover:text-red-300"
            aria-label="Dismiss error"
          >
            &times;
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-auto p-3">
        {(recycleLoading || workspaceTrashLoading) && recycleItems.length === 0 && workspaceTrashItems.length === 0 && (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary">
            <RefreshCw size={20} className="animate-spin" aria-hidden="true" />
          </div>
        )}

        {/* Container offline notices */}
        {recycleContainerOffline.map((agentName) => (
          <div key={agentName} className="mb-3 px-3 py-2 rounded-lg bg-zinc-500/10 border border-zinc-500/20 text-xs text-shell-text-tertiary">
            Agent stopped — recycle contents unavailable for <span className="font-medium">{agentName}</span>
          </div>
        ))}

        {!recycleLoading && !workspaceTrashLoading && recycleItems.length === 0 && workspaceTrashItems.length === 0 && recycleContainerOffline.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-shell-text-tertiary gap-2 text-center">
            <Recycle size={40} className="opacity-30" aria-hidden="true" />
            <span className="text-sm">Recycle bin is empty. Deleted files land here automatically.</span>
          </div>
        )}

        {/* Items deleted from the user's own Files workspace */}
        {workspaceTrashItems.length > 0 && (
          <section className="mb-4" aria-label="Trashed items from your workspace">
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary">
                My Files
              </span>
            </div>
            <div className="space-y-1.5">
              {workspaceTrashItems.map((item) => (
                <Card
                  key={item.id}
                  className="flex items-center gap-3 px-3 py-2.5 hover:bg-shell-surface/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs truncate font-mono" title={item.original_path}>
                      {item.original_path}
                    </div>
                    <div className="text-[10px] text-shell-text-tertiary mt-0.5">
                      {formatDate(Math.floor(new Date(item.deleted_at).getTime() / 1000))}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 hover:bg-emerald-500/15 hover:text-emerald-400"
                      onClick={() => handleWorkspaceTrashRestore(item)}
                      aria-label={`Restore ${item.original_path}`}
                      title="Restore"
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 hover:bg-red-500/15 hover:text-red-400"
                      onClick={() => handleWorkspaceTrashPurge(item)}
                      aria-label={`Permanently delete ${item.original_path}`}
                      title="Delete permanently"
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          </section>
        )}

        {Object.keys(recycleByAgent).map((agentName) => (
          <section key={agentName} className="mb-4" aria-label={`Recycle bin items for agent ${agentName}`}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary">
                {agentName}
              </span>
            </div>
            <div className="space-y-1.5">
              {recycleByAgent[agentName]!.map((item) => (
                <Card
                  key={item.id}
                  className="flex items-center gap-3 px-3 py-2.5 hover:bg-shell-surface/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs truncate font-mono" title={item.original_path}>
                      {item.original_path}
                    </div>
                    <div className="text-[10px] text-shell-text-tertiary mt-0.5">
                      {formatDate(Math.floor(new Date(item.deleted_at).getTime() / 1000))}
                    </div>
                  </div>
                  <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-shell-surface border border-white/5 text-shell-text-tertiary">
                    {item.agent_name}
                  </span>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 hover:bg-emerald-500/15 hover:text-emerald-400"
                      onClick={() => handleRecycleRestore(item)}
                      aria-label={`Restore ${item.original_path}`}
                      title="Restore"
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 hover:bg-red-500/15 hover:text-red-400"
                      onClick={() => handleRecycleDelete(item)}
                      aria-label={`Permanently delete ${item.original_path}`}
                      title="Delete permanently"
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );

  /* ---- Main file browser (detail pane) ---- */
  const mainContentUI = (
    <div className="w-full h-full flex flex-col min-w-0">
      {/* Toolbar — hidden on mobile when inside MobileSplitView (nav bar handles actions) */}
      {!isMobile && (
        <Toolbar className="shrink-0">
          {/* Left group — back button + breadcrumb trail. The
              ToolbarSpacer below grows to push the actions group to the
              right edge regardless of path depth (macOS Finder pattern). */}
          <div className="flex items-center gap-1 min-w-0 overflow-hidden">
            {currentPath && (
              <Button
                variant="ghost"
                size="icon"
                onClick={goUp}
                className="h-8 w-8 shrink-0"
                aria-label="Go up one directory"
              >
                <ArrowLeft size={16} />
              </Button>
            )}
            <nav
              className="flex items-center gap-1 text-xs min-w-0 flex-1 overflow-hidden"
              aria-label="File path"
            >
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigateTo("")}
                className={`h-7 px-2 shrink-0 ${!currentPath ? "text-shell-text font-medium" : ""}`}
              >
                {locationTitle}
              </Button>
              {pathSegments.map((seg, i) => {
                const segPath = pathSegments.slice(0, i + 1).join("/");
                const isLast = i === pathSegments.length - 1;
                return (
                  <span key={segPath} className="flex items-center gap-1 min-w-0">
                    <ChevronRight size={12} className="text-shell-text-tertiary shrink-0" />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigateTo(segPath)}
                      className={`h-7 px-2 truncate ${isLast ? "text-shell-text font-medium" : ""}`}
                    >
                      {seg}
                    </Button>
                  </span>
                );
              })}
            </nav>
          </div>

          <ToolbarSpacer />

          <ToolbarGroup className="shrink-0">{toolbarActions}</ToolbarGroup>
        </Toolbar>
      )}

      {/* Mobile breadcrumb + actions bar (shown inside detail pane) */}
      {isMobile && (
        <div className="shrink-0 flex items-center gap-1 px-3 py-2 border-b border-white/5">
          {currentPath && (
            <Button
              variant="ghost"
              size="icon"
              onClick={goUp}
              className="h-8 w-8 shrink-0"
              aria-label="Go up one directory"
            >
              <ArrowLeft size={16} />
            </Button>
          )}
          <nav className="flex items-center gap-1 text-xs min-w-0 flex-1 overflow-x-auto" aria-label="File path">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigateTo("")}
              className={`h-7 px-1.5 shrink-0 ${!currentPath ? "text-shell-text font-medium" : ""}`}
            >
              {locationTitle}
            </Button>
            {pathSegments.map((seg, i) => {
              const segPath = pathSegments.slice(0, i + 1).join("/");
              const isLast = i === pathSegments.length - 1;
              return (
                <span key={segPath} className="flex items-center gap-1 shrink-0">
                  <ChevronRight size={12} className="text-shell-text-tertiary shrink-0" />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigateTo(segPath)}
                    className={`h-7 px-1.5 shrink-0 ${isLast ? "text-shell-text font-medium" : ""}`}
                  >
                    {seg}
                  </Button>
                </span>
              );
            })}
          </nav>
          <div className="flex items-center gap-1 shrink-0">{toolbarActions}</div>
        </div>
      )}

      {/* ---- Error banner ---- */}
      {error && (
        <div className="mx-3 mt-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs">
          <AlertCircle size={14} className="shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)} className="hover:text-red-300" aria-label="Dismiss error">&times;</button>
        </div>
      )}

      {/* ---- Drop zone / file area ---- */}
      <div
        className={`flex-1 overflow-auto p-3 relative ${dragging ? "ring-2 ring-accent ring-inset bg-accent/5" : ""}`}
        onDragEnter={onDragEnter}
        onDragLeave={onDragLeave}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onContextMenu={(e) => {
          // Only the empty list background; rows/cards stopPropagation.
          if (!isWritable) return;
          e.preventDefault();
          setMenu({ x: e.clientX, y: e.clientY, file: null });
        }}
      >
        {/* Drop overlay */}
        {dragging && (
          <div className="absolute inset-0 flex items-center justify-center bg-shell-bg/80 z-10 pointer-events-none">
            <div className="flex flex-col items-center gap-2 text-accent">
              <Upload size={40} />
              <span className="text-sm font-medium">Drop files to upload</span>
            </div>
          </div>
        )}

        {/* Upload progress */}
        {uploading && (
          <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-lg bg-accent/10 text-accent text-xs">
            <RefreshCw size={14} className="animate-spin" />
            <span>Uploading...</span>
          </div>
        )}

        {/* Loading */}
        {loading && !uploading && (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary">
            <RefreshCw size={20} className="animate-spin" />
          </div>
        )}

        {/* Empty state */}
        {!loading && sortedFiles.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-full text-shell-text-tertiary gap-2">
            <Folder size={40} className="opacity-30" />
            <span className="text-sm">This folder is empty</span>
            <span className="text-xs">Drag files here or click Upload</span>
          </div>
        )}

        {/* ---- Grid view ---- */}
        {!loading && sortedFiles.length > 0 && viewMode === "grid" && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-2">
            {sortedFiles.map((f) => {
              const Icon = getFileIcon(f.name, f.is_dir);
              return (
                <Card
                  key={f.path || f.name}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setMenu({ x: e.clientX, y: e.clientY, file: f });
                  }}
                  className={`group relative bg-transparent border-transparent hover:bg-shell-surface hover:border-white/[0.06] transition-colors ${
                    clipboard?.mode === "cut" && clipboard.location === location && clipboard.path === f.path ? "opacity-50" : ""
                  }`}
                >
                <button
                  onClick={() => {
                    if (f.is_dir) {
                      navigateTo(f.path || (currentPath ? `${currentPath}/${f.name}` : f.name));
                    }
                  }}
                  onDoubleClick={() => {
                    if (!f.is_dir && isWritable) {
                      window.open(fileUrl(location, f.path || f.name), "_blank");
                    }
                  }}
                  className="flex flex-col items-center gap-2 p-3 text-center w-full rounded-xl"
                  aria-label={f.is_dir ? `Open folder ${f.name}` : `File ${f.name}`}
                >
                  {!f.is_dir && isImage(f.name) ? (
                    <div className="w-16 h-16 rounded-lg overflow-hidden bg-black/20 border border-white/[0.04] flex items-center justify-center">
                      <img
                        src={fileUrl(location, f.path || f.name)}
                        alt={f.name}
                        loading="lazy"
                        decoding="async"
                        className="w-full h-full object-cover"
                        onError={(e) => {
                          const target = e.currentTarget;
                          target.style.display = "none";
                          const fallback = target.nextElementSibling as HTMLElement | null;
                          if (fallback) fallback.style.display = "block";
                        }}
                      />
                      <Icon
                        size={36}
                        className="text-shell-text-secondary hidden"
                      />
                    </div>
                  ) : (
                    <Icon
                      size={36}
                      className={f.is_dir ? "text-accent" : "text-shell-text-secondary"}
                    />
                  )}
                  <span className="text-xs truncate w-full leading-tight" title={f.name}>
                    {f.name}
                  </span>
                  {!f.is_dir && isProjectLocation(location) && (
                    <ReviewBadge
                      state={reviewStates[f.path || f.name] ?? null}
                      onClick={
                        isProjectLocation(location)
                          ? () =>
                              cycleDocReview(
                                f.path || f.name,
                                reviewStates[f.path || f.name] ?? null,
                              )
                          : undefined
                      }
                    />
                  )}
                  {!f.is_dir && (
                    <span className="text-[10px] text-shell-text-tertiary">{formatSize(f.size)}</span>
                  )}

                  {/* Delete button overlay */}
                  {isWritable && (
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (deleteConfirm === f.path) {
                          handleDelete(f.path);
                        } else {
                          setDeleteConfirm(f.path);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.stopPropagation();
                          if (deleteConfirm === f.path) {
                            handleDelete(f.path);
                          } else {
                            setDeleteConfirm(f.path);
                          }
                        }
                      }}
                      className={`absolute top-1.5 right-1.5 p-1 rounded-md transition-all ${
                        deleteConfirm === f.path
                          ? "bg-red-500/20 text-red-400 opacity-100"
                          : "opacity-0 group-hover:opacity-100 hover:bg-red-500/20 text-shell-text-tertiary hover:text-red-400"
                      }`}
                      aria-label={deleteConfirm === f.path ? `Confirm delete ${f.name}` : `Delete ${f.name}`}
                      title={deleteConfirm === f.path ? "Click again to confirm" : "Delete"}
                    >
                      <Trash2 size={12} />
                    </span>
                  )}
                </button>
                </Card>
              );
            })}
          </div>
        )}

        {/* ---- List view ---- */}
        {!loading && sortedFiles.length > 0 && viewMode === "list" && (
          <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[520px]" aria-label="File list">
            <thead>
              <tr className="text-left text-shell-text-tertiary border-b border-white/5">
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium w-24">Size</th>
                <th className="px-3 py-2 font-medium w-32">Modified</th>
                <th className="px-3 py-2 font-medium w-28">Review</th>
                <th className="px-3 py-2 font-medium w-16">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedFiles.map((f) => (
                <FileRow
                  key={f.path || f.name}
                  f={f}
                  location={location}
                  currentPath={currentPath}
                  navigateTo={navigateTo}
                  isWritable={isWritable}
                  deleteConfirm={deleteConfirm}
                  handleDelete={handleDelete}
                  setDeleteConfirm={setDeleteConfirm}
                  onContextMenu={(e, file) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setMenu({ x: e.clientX, y: e.clientY, file });
                  }}
                  isCut={clipboard?.mode === "cut" && clipboard.location === location && clipboard.path === f.path}
                  reviewState={
                    !f.is_dir && isProjectLocation(location)
                      ? reviewStates[f.path || f.name] ?? null
                      : null
                  }
                  onDocReviewClick={
                    !f.is_dir && isProjectLocation(location)
                      ? () => cycleDocReview(f.path || f.name, reviewStates[f.path || f.name] ?? null)
                      : undefined
                  }
                />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </div>
  );

  /* ---- Context menu ---- */
  const openFile = useCallback((f: FileEntry) => {
    if (f.is_dir) {
      navigateTo(f.path || (currentPath ? `${currentPath}/${f.name}` : f.name));
      return;
    }
    const lowerName = f.name.toLowerCase();
    const isMarkdown = lowerName.endsWith(".md") || lowerName.endsWith(".markdown");
    if (isMarkdown && isProjectLocation(location)) {
      setDocViewer({ url: fileUrl(location, f.path || f.name), title: f.name });
      return;
    }
    if (isWritable) {
      window.open(fileUrl(location, f.path || f.name), "_blank");
    }
  }, [navigateTo, currentPath, isWritable, location]);

  const copySupported = workspaceCopyUrl(location) !== null;
  const moveSupported = workspaceRenameUrl(location) !== null;
  const pasteAcrossLocation = clipboard !== null && clipboard.location !== location;
  const pasteOpSupported = clipboard?.mode === "cut" ? moveSupported : copySupported;
  const canPaste = clipboard !== null && !pasteAcrossLocation && pasteOpSupported;

  const buildFileMenu = (f: FileEntry): MenuItem[] => {
    const items: MenuItem[] = [
      {
        label: "Open",
        icon: f.is_dir ? <FolderOpen size={14} /> : <FileText size={14} />,
        action: () => openFile(f),
      },
    ];
    if (!f.is_dir && isWritable) {
      items.push({
        label: "Download",
        icon: <Download size={14} />,
        action: () => window.open(fileUrl(location, f.path || f.name), "_blank"),
      });
    }
    if (isWritable) {
      items.push(
        { label: "", separator: true },
        { label: "Rename", icon: <Pencil size={14} />, action: () => handleRename(f) },
        {
          label: "Cut",
          icon: <Scissors size={14} />,
          disabled: !moveSupported,
          action: () => setClipboard({ mode: "cut", location, path: f.path, name: f.name }),
        },
        {
          label: "Copy",
          icon: <Copy size={14} />,
          disabled: !copySupported,
          action: () => setClipboard({ mode: "copy", location, path: f.path, name: f.name }),
        },
        { label: "", separator: true },
        {
          label: "Delete",
          icon: <Trash2 size={14} className="text-red-400" />,
          action: () => handleDelete(f.path),
        },
      );
    }
    return items;
  };

  const buildEmptyMenu = (): MenuItem[] => {
    if (!isWritable) return [];
    const pasteLabel = pasteAcrossLocation ? "Paste (unavailable here)" : "Paste";
    return [
      { label: "New Folder", icon: <FolderPlus size={14} />, action: handleNewFolder },
      {
        label: pasteLabel,
        icon: <ClipboardPaste size={14} />,
        disabled: !canPaste,
        action: handlePaste,
      },
      { label: "", separator: true },
      { label: "Upload", icon: <Upload size={14} />, action: () => fileInputRef.current?.click() },
    ];
  };

  /* ---- Root layout ---- */
  if (docViewer) {
    return (
      <DocViewer
        url={docViewer.url}
        title={docViewer.title}
        onClose={() => setDocViewer(null)}
      />
    );
  }

  return (
    <div className="flex h-full bg-shell-bg text-shell-text text-sm overflow-hidden">
      <MobileSplitView
        list={sidebarUI}
        detail={location === "recycle" ? recycleBinUI : mainContentUI}
        selectedId={selectedLocation}
        onBack={() => setSelectedLocation(null)}
        listTitle="Files"
        detailTitle={locationTitle}
        listWidth={208}
      />
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={menu.file ? buildFileMenu(menu.file) : buildEmptyMenu()}
        />
      )}
    </div>
  );
}
