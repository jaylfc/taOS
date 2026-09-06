import { useCallback, useEffect, useRef, useState } from "react";
import {
  FolderOpen,
  Folder,
  FileText,
  Plus,
  Save,
  ChevronRight,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
import { EditorState } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  drawSelection,
} from "@codemirror/view";
import { oneDark } from "@codemirror/theme-one-dark";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";

/* ── Types ──────────────────────────────────────────────── */

interface Workspace {
  id: string;
  name: string;
  path: string;
  created_at: string;
}

interface FileEntry {
  name: string;
  is_dir: boolean;
}

interface TreeNode {
  name: string;
  is_dir: boolean;
  subpath: string; // relative to workspace root
  expanded?: boolean;
  children?: TreeNode[];
  childrenLoaded?: boolean;
}

/* ── CodeMirror theme ───────────────────────────────────── */

const codeTheme = EditorView.theme({
  "&": {
    backgroundColor: "transparent",
    color: "rgba(255,255,255,0.85)",
    height: "100%",
    fontSize: "12.5px",
  },
  ".cm-content": {
    fontFamily: "'JetBrains Mono','Fira Mono','Menlo',monospace",
    lineHeight: "1.62",
    padding: "14px 0",
    caretColor: "#8b92a3",
  },
  ".cm-cursor": {
    borderLeftColor: "#8b92a3",
    borderLeftWidth: "2px",
  },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "rgba(139,146,163,0.25) !important",
  },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "rgba(255,255,255,0.18)",
    border: "none",
    minWidth: "46px",
    paddingRight: "4px",
    textAlign: "right",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "rgba(255,255,255,0.35)",
  },
  ".cm-activeLine": {
    backgroundColor: "rgba(255,255,255,0.025)",
  },
  ".cm-line": {
    padding: "0 16px",
  },
  ".cm-scroller": {
    overflow: "auto",
  },
});

/* ── CodeMirror editor component ────────────────────────── */

function CodeEditor({
  content,
  onChange,
}: {
  content: string;
  onChange: (v: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!containerRef.current) return;
    if (viewRef.current) {
      viewRef.current.destroy();
      viewRef.current = null;
    }
    const state = EditorState.create({
      doc: content,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        drawSelection(),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        codeTheme,
        oneDark,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current(update.state.doc.toString());
          }
        }),
        EditorView.lineWrapping,
      ],
    });
    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // re-create only when switching files (content identity swap from openFile)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content]);

  return <div ref={containerRef} className="h-full w-full" />;
}

/* ── File tree node ─────────────────────────────────────── */

function TreeItem({
  node,
  depth,
  activePath,
  onFileClick,
  onFolderToggle,
}: {
  node: TreeNode;
  depth: number;
  activePath: string | null;
  onFileClick: (path: string) => void;
  onFolderToggle: (node: TreeNode) => void;
}) {
  const isActive = !node.is_dir && node.subpath === activePath;
  const indent = depth * 12 + 8;

  if (node.is_dir) {
    return (
      <>
        <button
          type="button"
          onClick={() => onFolderToggle(node)}
          className="flex w-full cursor-pointer items-center gap-1.5 rounded-lg py-[5px] text-[12.5px] text-shell-text-secondary hover:bg-shell-surface"
          style={{ paddingLeft: indent }}
        >
          {node.expanded ? (
            <ChevronDown size={12} className="flex-none text-shell-text-tertiary" />
          ) : (
            <ChevronRight size={12} className="flex-none text-shell-text-tertiary" />
          )}
          {node.expanded ? (
            <FolderOpen size={13} className="flex-none text-shell-text-tertiary" />
          ) : (
            <Folder size={13} className="flex-none text-shell-text-tertiary" />
          )}
          <span className="truncate">{node.name}</span>
        </button>
        {node.expanded &&
          node.children?.map((child) => (
            <TreeItem
              key={child.subpath}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              onFileClick={onFileClick}
              onFolderToggle={onFolderToggle}
            />
          ))}
      </>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onFileClick(node.subpath)}
      className={`flex w-full cursor-pointer items-center gap-1.5 rounded-lg py-[5px] text-[12.5px] ${
        isActive
          ? "bg-shell-surface-active text-shell-text"
          : "text-shell-text-secondary hover:bg-shell-surface"
      }`}
      style={{ paddingLeft: indent + 16 }}
    >
      <FileText size={13} className="flex-none text-shell-text-tertiary" />
      <span className="truncate">{node.name}</span>
    </button>
  );
}

/* ── CodeView ────────────────────────────────────────────── */

export function CodeView() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wsLoading, setWsLoading] = useState(true);
  const [wsError, setWsError] = useState<string | null>(null);

  const [activeWs, setActiveWs] = useState<Workspace | null>(null);

  const [tree, setTree] = useState<TreeNode[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [treeError, setTreeError] = useState<string | null>(null);

  const [activePath, setActivePath] = useState<string | null>(null);
  // fileContent: initial content passed to the editor on file open (drives editor recreation)
  const [fileContent, setFileContent] = useState<string>("");
  // savedContent: last-saved baseline for dirty tracking (NOT passed to editor after initial load)
  const [savedContent, setSavedContent] = useState<string>("");
  const [editedContent, setEditedContent] = useState<string>("");
  // tracks the last-requested path so stale concurrent fetch responses are ignored
  const latestRequestedPath = useRef<string | null>(null);
  // ref so openFile callback can always read the current dirty state without stale closure
  const isDirtyRef = useRef(false);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);

  /* ── load workspaces ────────────────────────────────────── */

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

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  /* ── create workspace ───────────────────────────────────── */

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

  /* ── load file tree ─────────────────────────────────────── */

  const fetchChildren = useCallback(
    async (wsId: string, subpath: string): Promise<TreeNode[]> => {
      const qs = subpath ? `?subpath=${encodeURIComponent(subpath)}` : "";
      const res = await fetch(`/api/coding/workspaces/${wsId}/files${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const entries: FileEntry[] = await res.json();
      return entries.map((e) => ({
        name: e.name,
        is_dir: e.is_dir,
        subpath: subpath ? `${subpath}/${e.name}` : e.name,
        expanded: false,
        children: [],
        childrenLoaded: false,
      }));
    },
    [],
  );

  useEffect(() => {
    if (!activeWs) return;
    setTree([]);
    setActivePath(null);
    setFileContent("");
    setSavedContent("");
    setEditedContent("");
    setTreeError(null);
    setTreeLoading(true);
    fetchChildren(activeWs.id, "")
      .then((nodes) => setTree(nodes))
      .catch((err) =>
        setTreeError(err instanceof Error ? err.message : "Failed to load files"),
      )
      .finally(() => setTreeLoading(false));
  }, [activeWs, fetchChildren]);

  /* ── expand / collapse folder ───────────────────────────── */

  const toggleFolder = useCallback(
    async (target: TreeNode) => {
      if (!activeWs) return;

      // If collapsing, just toggle
      if (target.expanded) {
        const collapse = (nodes: TreeNode[]): TreeNode[] =>
          nodes.map((n) =>
            n.subpath === target.subpath
              ? { ...n, expanded: false }
              : { ...n, children: collapse(n.children ?? []) },
          );
        setTree((prev) => collapse(prev));
        return;
      }

      // Load children if not yet loaded
      let children = target.children ?? [];
      if (!target.childrenLoaded) {
        try {
          children = await fetchChildren(activeWs.id, target.subpath);
        } catch {
          setTreeError("Failed to expand folder");
          return;
        }
      }

      const expand = (nodes: TreeNode[]): TreeNode[] =>
        nodes.map((n) =>
          n.subpath === target.subpath
            ? { ...n, expanded: true, children, childrenLoaded: true }
            : { ...n, children: expand(n.children ?? []) },
        );
      setTree((prev) => expand(prev));
    },
    [activeWs, fetchChildren],
  );

  /* ── open file ──────────────────────────────────────────── */

  const openFile = useCallback(
    async (path: string) => {
      if (!activeWs) return;
      if (isDirtyRef.current) {
        const ok = window.confirm(
          "You have unsaved changes. Discard them and open the new file?",
        );
        if (!ok) return;
      }
      latestRequestedPath.current = path;
      setActivePath(path);
      setFileLoading(true);
      setFileError(null);
      setSaveError(null);
      setSavedOk(false);
      try {
        const res = await fetch(
          `/api/coding/workspaces/${activeWs.id}/file?path=${encodeURIComponent(path)}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: { path: string; content: string } = await res.json();
        // discard response if a newer openFile call has already taken over
        if (latestRequestedPath.current !== path) return;
        setFileContent(data.content);
        setSavedContent(data.content);
        setEditedContent(data.content);
      } catch (err) {
        if (latestRequestedPath.current !== path) return;
        setFileError(err instanceof Error ? err.message : "Failed to load file");
        setFileLoading(false);
        return;
      }
      setFileLoading(false);
    },
    [activeWs],
  );

  /* ── save file ──────────────────────────────────────────── */

  const saveFile = useCallback(async () => {
    if (!activeWs || !activePath) return;
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      const res = await fetch(`/api/coding/workspaces/${activeWs.id}/file`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: activePath, content: editedContent }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // update savedContent, not fileContent -- fileContent drives editor recreation
      // so touching it after the initial load would destroy cursor position + undo history
      setSavedContent(editedContent);
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [activeWs, activePath, editedContent]);

  const isDirty = editedContent !== savedContent;
  isDirtyRef.current = isDirty;

  /* ── render ─────────────────────────────────────────────── */

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Code</h2>

        {/* workspace picker */}
        <div className="flex items-center gap-2">
          {wsLoading ? (
            <span className="text-[12px] text-shell-text-tertiary">Loading...</span>
          ) : (
            <>
              {wsError && (
                <span className="flex items-center gap-1 text-[12px] text-red-400">
                  <AlertCircle size={13} />
                  {wsError}
                </span>
              )}
              <select
                aria-label="Select workspace"
                value={activeWs?.id ?? ""}
                onChange={(e) => {
                  const ws = workspaces.find((w) => w.id === e.target.value) ?? null;
                  if (isDirty) {
                    const ok = window.confirm(
                      "You have unsaved changes. Discard them and switch workspace?",
                    );
                    if (!ok) return;
                  }
                  setActiveWs(ws);
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
            onClick={createWorkspace}
            aria-label="New workspace"
            className="flex h-[28px] items-center gap-1.5 rounded-[9px] border border-shell-border bg-shell-surface px-2.5 text-[12px] text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
          >
            <Plus size={13} />
            New workspace
          </button>
        </div>

        {/* save bar */}
        {activePath && (
          <div className="ml-auto flex items-center gap-2.5">
            {saveError && (
              <span className="flex items-center gap-1 text-[11.5px] text-red-400">
                <AlertCircle size={12} />
                {saveError}
              </span>
            )}
            {savedOk && (
              <span className="text-[11.5px] text-green-400">Saved</span>
            )}
            <button
              type="button"
              onClick={saveFile}
              disabled={saving || !isDirty}
              aria-label="Save file"
              className={`flex h-[28px] items-center gap-1.5 rounded-[9px] border px-2.5 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40 ${
                isDirty && !saving
                  ? "border-accent/40 bg-accent/15 text-accent hover:bg-accent/25"
                  : "border-shell-border bg-shell-surface text-shell-text-tertiary"
              }`}
            >
              <Save size={13} />
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        )}
      </div>

      {/* body */}
      <div className="flex min-h-0 flex-1">
        {/* file tree */}
        <div className="flex w-[190px] flex-none flex-col overflow-auto border-r border-shell-border bg-shell-bg-deep px-2 py-2.5">
          {!activeWs ? (
            <div className="px-2 py-3 text-[11.5px] text-shell-text-tertiary">
              Select or create a workspace
            </div>
          ) : treeLoading ? (
            <div className="px-2 py-3 text-[11.5px] text-shell-text-tertiary">Loading...</div>
          ) : treeError ? (
            <div className="flex items-center gap-1 px-2 py-2 text-[11.5px] text-red-400">
              <AlertCircle size={12} />
              {treeError}
            </div>
          ) : tree.length === 0 ? (
            <div className="px-2 py-3 text-[11.5px] text-shell-text-tertiary">
              Empty workspace
            </div>
          ) : (
            <>
              <div className="px-2 pb-2 pt-1 text-[10.5px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
                {activeWs.name}
              </div>
              {tree.map((node) => (
                <TreeItem
                  key={node.subpath}
                  node={node}
                  depth={0}
                  activePath={activePath}
                  onFileClick={openFile}
                  onFolderToggle={toggleFolder}
                />
              ))}
            </>
          )}
        </div>

        {/* editor pane */}
        <div className="flex min-w-0 flex-1 flex-col bg-[#1a1b2e]">
          {!activePath ? (
            <div className="flex flex-1 items-center justify-center">
              <span className="text-[13px] text-shell-text-tertiary">
                {activeWs ? "Select a file to edit" : "Select a workspace first"}
              </span>
            </div>
          ) : fileLoading ? (
            <div className="flex flex-1 items-center justify-center">
              <span className="text-[12px] text-shell-text-tertiary">Loading...</span>
            </div>
          ) : fileError ? (
            <div className="flex flex-1 items-center justify-center gap-2 text-[12px] text-red-400">
              <AlertCircle size={14} />
              {fileError}
            </div>
          ) : (
            <>
              {/* file tab bar */}
              <div className="flex h-[38px] flex-none items-stretch border-b border-shell-border bg-shell-bg-deep">
                <div className="flex cursor-default items-center gap-2 border-r border-shell-border bg-[#1a1b2e] px-4 text-[12px] text-shell-text shadow-[inset_0_-2px_0_0_var(--color-accent,#8b92a3)]">
                  <FileText size={12} className="text-shell-text-tertiary" />
                  {activePath.split("/").pop()}
                  {isDirty && (
                    <span className="h-1.5 w-1.5 flex-none rounded-full bg-amber-400" />
                  )}
                </div>
              </div>

              {/* codemirror */}
              <div className="min-h-0 flex-1 overflow-hidden">
                <CodeEditor
                  key={activePath}
                  content={fileContent}
                  onChange={setEditedContent}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
