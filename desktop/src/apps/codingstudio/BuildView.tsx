import { useCallback, useEffect, useRef, useState } from "react";
import {
  Sparkles,
  Loader2,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Plus,
  ChevronDown,
  ChevronRight,
  Check,
  X,
  GitBranch,
  FolderDown,
} from "lucide-react";
import { streamTaosAgentChat } from "../appstudio/stream-chat";

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

export interface Workspace {
  id: string;
  name: string;
  path: string;
  created_at: string;
}

interface DiffEntry {
  path: string;
  status: "added" | "modified" | "deleted";
  patch: string;
}

type StepKind = "info" | "error" | "done";

interface BuildStep {
  id: number;
  kind: StepKind;
  text: string;
}

interface ParsedBlock {
  path: string;
  lang: string;
  content: string;
}

type ApplyStatus = "idle" | "applying" | "applied" | "error";

interface BlockApplyState {
  status: ApplyStatus;
  error?: string;
}

/* ------------------------------------------------------------------ */
/*  Parse fenced code blocks from agent output                          */
/*                                                                      */
/*  Supported annotation formats (first line after opening fence):      */
/*    // path: src/App.tsx                                              */
/*    # path: src/App.tsx                                               */
/*    // src/App.tsx                                                     */
/*    path: src/App.tsx                                                  */
/*  Or the language token itself as a filename hint when it contains    */
/*  a slash: ```src/components/Button.tsx                               */
/* ------------------------------------------------------------------ */

const PATH_ANNOTATION_RE = /^(?:\/\/|#)?\s*(?:path:\s*)?(\S+\.\w+)\s*$/;

function parseCodeBlocks(text: string): ParsedBlock[] {
  const blocks: ParsedBlock[] = [];
  // Match ```lang\n...``` with optional lang that may be a path
  const fenceRe = /```(\S*)\n([\s\S]*?)```/g;
  let m: RegExpExecArray | null;
  while ((m = fenceRe.exec(text)) !== null) {
    const langToken = m[1] ?? "";
    const rawBody = m[2] ?? "";
    let detectedPath: string | null = null;
    let content = rawBody;

    // Check if the lang token itself is a file path (contains a dot and
    // possibly a slash, e.g. ```src/App.tsx)
    if (langToken.includes(".") && (langToken.includes("/") || /\.\w{1,6}$/.test(langToken))) {
      detectedPath = langToken;
    }

    // Inspect first line of body for a path annotation
    if (!detectedPath) {
      const firstLine = rawBody.split("\n")[0] ?? "";
      const annotMatch = PATH_ANNOTATION_RE.exec(firstLine.trim());
      if (annotMatch) {
        detectedPath = annotMatch[1] ?? null;
        // Strip the annotation line from content
        content = rawBody.slice(firstLine.length + 1);
      }
    }

    if (!detectedPath) continue;

    // Skip paths that look like shell commands or URLs
    if (detectedPath.startsWith("http") || detectedPath.startsWith("$")) continue;

    blocks.push({
      path: detectedPath,
      lang: langToken.includes("/") ? "" : langToken,
      content,
    });
  }
  return blocks;
}

/* ------------------------------------------------------------------ */
/*  Workspace selector (mirrors CodeView pattern)                       */
/* ------------------------------------------------------------------ */

function WorkspaceSelector({
  workspaces,
  loading,
  error,
  value,
  onChange,
  onNew,
}: {
  workspaces: Workspace[];
  loading: boolean;
  error: string | null;
  value: Workspace | null;
  onChange: (ws: Workspace | null) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      {loading ? (
        <span className="text-[12px] text-shell-text-tertiary">Loading...</span>
      ) : (
        <>
          {error && (
            <span className="flex items-center gap-1 text-[12px] text-red-400">
              <AlertCircle size={13} />
              {error}
            </span>
          )}
          <select
            aria-label="Select workspace"
            value={value?.id ?? ""}
            onChange={(e) => {
              const ws = workspaces.find((w) => w.id === e.target.value) ?? null;
              onChange(ws);
            }}
            className="h-[28px] rounded-[9px] border border-shell-border bg-shell-surface px-2 text-[12px] text-shell-text focus:outline-none focus:ring-1 focus:ring-accent/40"
          >
            <option value="">-- select workspace --</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name}
              </option>
            ))}
          </select>
        </>
      )}
      <button
        type="button"
        onClick={onNew}
        aria-label="New workspace"
        className="flex h-[28px] items-center gap-1.5 rounded-[9px] border border-shell-border bg-shell-surface px-2.5 text-[12px] text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
      >
        <Plus size={13} />
        New workspace
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Diff patch display (read-only, styled +/- lines)                    */
/* ------------------------------------------------------------------ */

function PatchView({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <div
      className="overflow-auto rounded-[10px] bg-shell-bg-deep p-2 font-mono text-[11px] leading-[1.65]"
      style={{ maxHeight: 220 }}
    >
      {lines.map((line, i) => {
        let cls = "text-shell-text-secondary";
        if (line.startsWith("+") && !line.startsWith("+++")) cls = "text-green-400";
        if (line.startsWith("-") && !line.startsWith("---")) cls = "text-red-400";
        if (line.startsWith("@@")) cls = "text-blue-400";
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Diff review panel                                                   */
/* ------------------------------------------------------------------ */

function DiffReview({
  entries,
  accepting,
  reverting,
  onAccept,
  onRevert,
  onAcceptAll,
  onRevertAll,
}: {
  entries: DiffEntry[];
  accepting: Set<string>;
  reverting: Set<string>;
  onAccept: (path: string) => void;
  onRevert: (path: string) => void;
  onAcceptAll: () => void;
  onRevertAll: () => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  if (entries.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-[12px] border border-shell-border bg-shell-surface px-4 py-3 text-[12px] text-shell-text-tertiary">
        <CheckCircle2 size={15} className="text-green-400" />
        No uncommitted changes
      </div>
    );
  }

  const statusColor = (s: DiffEntry["status"]) => {
    if (s === "added") return "text-green-400";
    if (s === "deleted") return "text-red-400";
    return "text-amber-400";
  };

  const statusLabel = (s: DiffEntry["status"]) => {
    if (s === "added") return "A";
    if (s === "deleted") return "D";
    return "M";
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-semibold text-shell-text">
          {entries.length} changed file{entries.length !== 1 ? "s" : ""}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={onRevertAll}
            aria-label="Reject all changes"
            className="flex h-[26px] items-center gap-1.5 rounded-[8px] border border-red-500/30 bg-red-500/10 px-2.5 text-[11px] font-semibold text-red-300 hover:bg-red-500/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-red-400/40"
          >
            <X size={12} />
            Reject all
          </button>
          <button
            type="button"
            onClick={onAcceptAll}
            aria-label="Accept all changes"
            className="flex h-[26px] items-center gap-1.5 rounded-[8px] border border-green-500/30 bg-green-500/10 px-2.5 text-[11px] font-semibold text-green-300 hover:bg-green-500/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-green-400/40"
          >
            <Check size={12} />
            Accept all
          </button>
        </div>
      </div>

      {entries.map((entry) => {
        const open = expanded.has(entry.path);
        const busy = accepting.has(entry.path) || reverting.has(entry.path);
        return (
          <div
            key={entry.path}
            className="rounded-[12px] border border-shell-border bg-shell-surface"
          >
            <div className="flex items-center gap-2 px-3 py-2.5">
              <button
                type="button"
                onClick={() => toggle(entry.path)}
                aria-label={open ? "Collapse diff" : "Expand diff"}
                className="flex flex-none items-center text-shell-text-tertiary"
              >
                {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <span className={`w-4 flex-none text-center text-[11px] font-bold ${statusColor(entry.status)}`}>
                {statusLabel(entry.status)}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-shell-text">
                {entry.path}
              </span>
              <div className="flex flex-none gap-1.5">
                <button
                  type="button"
                  onClick={() => onRevert(entry.path)}
                  disabled={busy}
                  aria-label={`Reject changes to ${entry.path}`}
                  className="flex h-[24px] items-center gap-1 rounded-[7px] border border-red-500/30 bg-red-500/10 px-2 text-[10.5px] font-semibold text-red-300 hover:bg-red-500/20 disabled:opacity-40 focus-visible:outline-none"
                >
                  {reverting.has(entry.path) ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <XCircle size={11} />
                  )}
                  Reject
                </button>
                <button
                  type="button"
                  onClick={() => onAccept(entry.path)}
                  disabled={busy}
                  aria-label={`Accept changes to ${entry.path}`}
                  className="flex h-[24px] items-center gap-1 rounded-[7px] border border-green-500/30 bg-green-500/10 px-2 text-[10.5px] font-semibold text-green-300 hover:bg-green-500/20 disabled:opacity-40 focus-visible:outline-none"
                >
                  {accepting.has(entry.path) ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <CheckCircle2 size={11} />
                  )}
                  Accept
                </button>
              </div>
            </div>
            {open && entry.patch && (
              <div className="border-t border-shell-border px-3 pb-3 pt-2">
                <PatchView patch={entry.patch} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Apply-blocks panel: shows detected files + apply button            */
/* ------------------------------------------------------------------ */

function ApplyBlocksPanel({
  blocks,
  workspaceId,
  onApplied,
}: {
  blocks: ParsedBlock[];
  workspaceId: string;
  onApplied: () => void;
}) {
  const [states, setStates] = useState<Record<string, BlockApplyState>>({});
  const [applying, setApplying] = useState(false);

  const handleApply = useCallback(async () => {
    setApplying(true);
    const next: Record<string, BlockApplyState> = {};
    for (const b of blocks) next[b.path] = { status: "applying" };
    setStates(next);

    try {
      const res = await fetch(`/api/coding/workspaces/${workspaceId}/apply-blocks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          blocks: blocks.map((b) => ({ path: b.path, content: b.content })),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg = (data as { error?: string }).error ?? `HTTP ${res.status}`;
        const errStates: Record<string, BlockApplyState> = {};
        for (const b of blocks) errStates[b.path] = { status: "error", error: msg };
        setStates(errStates);
        return;
      }
      const data = (await res.json()) as { applied: string[] };
      const applied = new Set(data.applied);
      const doneStates: Record<string, BlockApplyState> = {};
      for (const b of blocks) {
        doneStates[b.path] = applied.has(b.path)
          ? { status: "applied" }
          : { status: "error", error: "not confirmed by server" };
      }
      setStates(doneStates);
      onApplied();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const errStates: Record<string, BlockApplyState> = {};
      for (const b of blocks) errStates[b.path] = { status: "error", error: msg };
      setStates(errStates);
    } finally {
      setApplying(false);
    }
  }, [blocks, workspaceId, onApplied]);

  const allDone = blocks.length > 0 && blocks.every((b) => states[b.path]?.status === "applied");

  return (
    <div className="flex flex-col gap-2 rounded-[12px] border border-accent/30 bg-accent/5 p-3">
      <div className="flex items-center gap-2">
        <FolderDown size={14} className="text-accent" />
        <span className="text-[12px] font-semibold text-shell-text">
          {blocks.length} file{blocks.length !== 1 ? "s" : ""} detected in response
        </span>
        {!allDone && (
          <button
            type="button"
            onClick={() => void handleApply()}
            disabled={applying}
            aria-label="Apply files to workspace"
            className="ml-auto flex h-[26px] items-center gap-1.5 rounded-[8px] border border-accent/40 bg-accent/20 px-3 text-[11px] font-semibold text-accent hover:bg-accent/30 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
          >
            {applying ? <Loader2 size={11} className="animate-spin" /> : <FolderDown size={11} />}
            Apply to workspace
          </button>
        )}
        {allDone && (
          <span className="ml-auto flex items-center gap-1 text-[11px] font-semibold text-green-400">
            <CheckCircle2 size={12} />
            Applied
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1">
        {blocks.map((b) => {
          const st = states[b.path];
          return (
            <div
              key={b.path}
              className="flex items-center gap-2 rounded-[8px] bg-shell-bg-deep px-2.5 py-1.5"
            >
              {st?.status === "applying" && <Loader2 size={11} className="flex-none animate-spin text-accent" />}
              {st?.status === "applied" && <CheckCircle2 size={11} className="flex-none text-green-400" />}
              {st?.status === "error" && <XCircle size={11} className="flex-none text-red-400" />}
              {(!st || st.status === "idle") && (
                <span className="h-[11px] w-[11px] flex-none" />
              )}
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-shell-text-secondary">
                {b.path}
              </span>
              {st?.status === "error" && st.error && (
                <span className="text-[10px] text-red-400">{st.error}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BuildView                                                            */
/* ------------------------------------------------------------------ */

let _stepId = 0;
const nextId = () => ++_stepId;

export function BuildView({
  onActiveWorkspaceChange,
}: {
  onActiveWorkspaceChange?: (ws: Workspace | null) => void;
} = {}) {
  // Workspaces
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wsLoading, setWsLoading] = useState(true);
  const [wsError, setWsError] = useState<string | null>(null);
  const [activeWs, setActiveWs] = useState<Workspace | null>(null);

  // Surface the active workspace up to the parent (e.g. so Preview can load it).
  useEffect(() => {
    onActiveWorkspaceChange?.(activeWs);
  }, [activeWs, onActiveWorkspaceChange]);

  // Build
  const [prompt, setPrompt] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [steps, setSteps] = useState<BuildStep[]>([]);
  const [buildError, setBuildError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Detected code blocks from last completed stream
  const [parsedBlocks, setParsedBlocks] = useState<ParsedBlock[]>([]);

  // Diff review
  const [diffEntries, setDiffEntries] = useState<DiffEntry[]>([]);
  const [diffLoading, setDiffLoading] = useState(false);
  const [accepting, setAccepting] = useState<Set<string>>(new Set());
  const [reverting, setReverting] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<"chat" | "diff">("chat");

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  // Load workspaces
  const loadWorkspaces = useCallback(async () => {
    setWsLoading(true);
    setWsError(null);
    try {
      const res = await fetch("/api/coding/workspaces");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: Workspace[] = await res.json();
      setWorkspaces(data);
    } catch (err) {
      setWsError(err instanceof Error ? err.message : "Failed to load workspaces");
    } finally {
      setWsLoading(false);
    }
  }, []);

  useEffect(() => { void loadWorkspaces(); }, [loadWorkspaces]);

  // Load agent model
  useEffect(() => {
    fetch("/api/taos-agent/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (data?.model !== undefined) setModel(data.model); })
      .catch(() => {});
  }, []);

  // Scroll log to bottom as steps arrive
  useEffect(() => {
    if (!streaming) return;
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps, streaming]);

  // Create workspace
  const createWorkspace = useCallback(async () => {
    const name = window.prompt("Workspace name:");
    if (!name?.trim()) return;
    try {
      const res = await fetch("/api/coding/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const ws: Workspace = await res.json();
      setWorkspaces((prev) => [...prev, ws]);
      setActiveWs(ws);
    } catch (err) {
      setWsError(err instanceof Error ? err.message : "Failed to create workspace");
    }
  }, []);

  // Fetch diff from backend
  const fetchDiff = useCallback(async (wsId: string) => {
    setDiffLoading(true);
    try {
      const res = await fetch(`/api/coding/workspaces/${wsId}/diff`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DiffEntry[] = await res.json();
      setDiffEntries(data);
    } catch {
      setDiffEntries([]);
    } finally {
      setDiffLoading(false);
    }
  }, []);

  // Called after apply-blocks writes files; refresh diff and switch tab
  const handleApplied = useCallback(() => {
    if (activeWs) {
      void fetchDiff(activeWs.id);
      setTab("diff");
    }
  }, [activeWs, fetchDiff]);

  // Handle build submit
  const handleBuild = useCallback(async () => {
    if (streaming || !model || !activeWs) return;
    const text = prompt.trim();
    if (!text) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setSteps([]);
    setBuildError(null);
    setParsedBlocks([]);
    setStreaming(true);
    setTab("chat");

    const systemContext = `You are a coding assistant for taOS Coding Studio.
The user has selected workspace: "${activeWs.name}" (id: ${activeWs.id}).
When you write files, use fenced code blocks and annotate the filename on the first line of the block like this:
\`\`\`tsx
// path: src/App.tsx
<file content here>
\`\`\`
Keep responses concise and practical.`;

    const userMessage = `${systemContext}\n\nUser request: ${text}`;

    const outputChunks: string[] = [];

    try {
      await streamTaosAgentChat(
        [{ role: "user", content: userMessage }],
        (delta) => {
          if (!mountedRef.current) return;
          outputChunks.push(delta);
          const full = outputChunks.join("");
          setSteps([
            { id: nextId(), kind: "info", text: full },
          ]);
        },
        (message) => {
          if (!mountedRef.current) return;
          setBuildError(message);
        },
        { signal: controller.signal },
      );
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError") && mountedRef.current) {
        setBuildError(String(e));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (mountedRef.current) {
        setStreaming(false);
        const full = outputChunks.join("");
        const blocks = parseCodeBlocks(full);
        setParsedBlocks(blocks);
        // Only auto-switch to diff tab if no blocks were detected;
        // when blocks are present the user should apply them first.
        if (blocks.length === 0) {
          void fetchDiff(activeWs.id);
          setTab("diff");
        }
      }
    }
  }, [prompt, streaming, model, activeWs, fetchDiff]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void handleBuild();
      }
    },
    [handleBuild],
  );

  // Switch to diff tab and refresh
  const openDiff = useCallback(() => {
    setTab("diff");
    if (activeWs) void fetchDiff(activeWs.id);
  }, [activeWs, fetchDiff]);

  // Accept a single file's changes
  const acceptFile = useCallback(async (path: string) => {
    if (!activeWs) return;
    setAccepting((prev) => new Set([...prev, path]));
    try {
      const res = await fetch(`/api/coding/workspaces/${activeWs.id}/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: [path] }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDiffEntries((prev) => prev.filter((e) => e.path !== path));
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : "Accept failed");
    } finally {
      setAccepting((prev) => { const n = new Set(prev); n.delete(path); return n; });
    }
  }, [activeWs]);

  // Revert a single file's changes
  const revertFile = useCallback(async (path: string) => {
    if (!activeWs) return;
    setReverting((prev) => new Set([...prev, path]));
    try {
      const res = await fetch(`/api/coding/workspaces/${activeWs.id}/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: [path] }),
      });
      if (res.status === 207) {
        const data = await res.json();
        const failed: string[] = data.failed ?? [];
        if (failed.length > 0) {
          setBuildError(`Revert failed for: ${failed.join(", ")}`);
        } else {
          setDiffEntries((prev) => prev.filter((e) => e.path !== path));
        }
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDiffEntries((prev) => prev.filter((e) => e.path !== path));
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : "Revert failed");
    } finally {
      setReverting((prev) => { const n = new Set(prev); n.delete(path); return n; });
    }
  }, [activeWs]);

  // Accept all
  const acceptAll = useCallback(async () => {
    if (!activeWs || diffEntries.length === 0) return;
    const paths = diffEntries.map((e) => e.path);
    const allSet = new Set(paths);
    setAccepting(allSet);
    try {
      const res = await fetch(`/api/coding/workspaces/${activeWs.id}/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDiffEntries([]);
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : "Accept all failed");
    } finally {
      setAccepting(new Set());
    }
  }, [activeWs, diffEntries]);

  // Revert all
  const revertAll = useCallback(async () => {
    if (!activeWs || diffEntries.length === 0) return;
    const paths = diffEntries.map((e) => e.path);
    const allSet = new Set(paths);
    setReverting(allSet);
    try {
      const res = await fetch(`/api/coding/workspaces/${activeWs.id}/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      });
      if (res.status === 207) {
        const data = await res.json();
        const failed: string[] = data.failed ?? [];
        const reverted: string[] = data.reverted ?? [];
        setDiffEntries((prev) => prev.filter((e) => !reverted.includes(e.path)));
        if (failed.length > 0) {
          setBuildError(`Revert failed for: ${failed.join(", ")}`);
        }
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDiffEntries([]);
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : "Revert all failed");
    } finally {
      setReverting(new Set());
    }
  }, [activeWs, diffEntries]);

  const noModel = !model;
  const noWorkspace = !activeWs;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Build</h2>

        <WorkspaceSelector
          workspaces={workspaces}
          loading={wsLoading}
          error={wsError}
          value={activeWs}
          onChange={setActiveWs}
          onNew={createWorkspace}
        />

        <div className="ml-auto flex rounded-full border border-shell-border bg-shell-surface p-[3px]">
          <button
            type="button"
            aria-pressed={tab === "chat"}
            onClick={() => setTab("chat")}
            className={`rounded-full px-[13px] py-[5px] text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40 ${
              tab === "chat"
                ? "bg-shell-surface-active text-shell-text"
                : "text-shell-text-secondary hover:text-shell-text"
            }`}
          >
            Chat
          </button>
          <button
            type="button"
            aria-pressed={tab === "diff"}
            onClick={openDiff}
            className={`relative rounded-full px-[13px] py-[5px] text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40 ${
              tab === "diff"
                ? "bg-shell-surface-active text-shell-text"
                : "text-shell-text-secondary hover:text-shell-text"
            }`}
          >
            Diff
            {diffEntries.length > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-[9px] font-bold text-white">
                {diffEntries.length}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* main area */}
      <div className="flex min-h-0 flex-1 flex-col overflow-auto px-[22px] py-5">
        {tab === "chat" ? (
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            {/* no workspace banner */}
            {noWorkspace && (
              <div className="rounded-[12px] border border-shell-border bg-shell-surface px-4 py-3 text-[12px] text-shell-text-tertiary">
                Select or create a workspace above to get started.
              </div>
            )}

            {/* no model banner */}
            {!noWorkspace && noModel && (
              <div className="rounded-[12px] border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[12px] text-amber-300">
                No model selected. Go to taOS Assistant settings to pick a model.
              </div>
            )}

            {/* error */}
            {buildError && (
              <div
                className="rounded-[12px] border border-red-500/30 bg-red-500/10 px-4 py-3 text-[12px] text-red-300"
                role="alert"
              >
                {buildError}
              </div>
            )}

            {/* steps / output */}
            {steps.length === 0 && !streaming && !buildError && (
              <div className="flex flex-1 items-center justify-center">
                <p className="max-w-[320px] text-center text-[12.5px] leading-relaxed text-shell-text-tertiary">
                  Describe what you want to build. The agent will respond with a plan and code.
                  When it emits files, an Apply button appears below to write them to the workspace.
                  Switch to the Diff tab to review and accept or reject changes.
                </p>
              </div>
            )}

            {steps.map((step) => (
              <div
                key={streaming ? "stream-output" : step.id}
                className="rounded-[12px] border border-shell-border bg-shell-surface px-4 py-3"
              >
                <pre className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-shell-text-secondary">
                  {step.text}
                  {streaming && (
                    <span
                      aria-hidden
                      className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-accent align-[-1px]"
                    />
                  )}
                </pre>
              </div>
            ))}

            {streaming && steps.length === 0 && (
              <div className="flex items-center gap-2 text-[12px] text-shell-text-tertiary">
                <Loader2 size={14} className="animate-spin" />
                Thinking...
              </div>
            )}

            {/* Apply-blocks panel: shown after stream completes if files were detected */}
            {!streaming && parsedBlocks.length > 0 && activeWs && (
              <ApplyBlocksPanel
                blocks={parsedBlocks}
                workspaceId={activeWs.id}
                onApplied={handleApplied}
              />
            )}

            <div ref={logEndRef} />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex items-center gap-2">
              <GitBranch size={15} className="text-accent" />
              <span className="text-[13px] font-semibold">Diff review</span>
              {activeWs && (
                <span className="text-[12px] text-shell-text-tertiary">
                  {activeWs.name}
                </span>
              )}
              {activeWs && (
                <button
                  type="button"
                  onClick={() => void fetchDiff(activeWs.id)}
                  disabled={diffLoading}
                  aria-label="Refresh diff"
                  className="ml-auto flex h-[26px] items-center gap-1.5 rounded-[8px] border border-shell-border bg-shell-surface px-2.5 text-[11px] text-shell-text-secondary hover:bg-shell-surface-active disabled:opacity-50 focus-visible:outline-none"
                >
                  {diffLoading ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    "Refresh"
                  )}
                </button>
              )}
            </div>

            {!activeWs ? (
              <div className="rounded-[12px] border border-shell-border bg-shell-surface px-4 py-3 text-[12px] text-shell-text-tertiary">
                Select a workspace to view its diff.
              </div>
            ) : diffLoading ? (
              <div className="flex items-center gap-2 text-[12px] text-shell-text-tertiary">
                <Loader2 size={13} className="animate-spin" />
                Loading diff...
              </div>
            ) : (
              <DiffReview
                entries={diffEntries}
                accepting={accepting}
                reverting={reverting}
                onAccept={acceptFile}
                onRevert={revertFile}
                onAcceptAll={acceptAll}
                onRevertAll={revertAll}
              />
            )}
          </div>
        )}
      </div>

      {/* prompt bar */}
      <div className="flex flex-none items-end gap-3 border-t border-shell-border bg-shell-bg-deep px-[22px] py-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          disabled={streaming}
          placeholder={
            noWorkspace
              ? "Select a workspace first..."
              : "Describe what you want to build or change... (Cmd+Enter to send)"
          }
          className="min-h-[50px] flex-1 resize-none rounded-[15px] border border-shell-border bg-shell-surface px-4 py-3 text-[13.5px] text-shell-text-secondary placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={() => void handleBuild()}
          disabled={streaming || !prompt.trim() || noModel || noWorkspace}
          aria-label="Run build"
          className="flex h-[50px] flex-none items-center gap-[9px] rounded-[15px] border-none px-6 text-[14px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            background: "linear-gradient(135deg,var(--color-accent),var(--color-accent))",
          }}
        >
          {streaming ? <Loader2 size={18} className="animate-spin" /> : <Sparkles size={18} />}
          {streaming ? "Building..." : "Build"}
        </button>
      </div>
    </div>
  );
}
