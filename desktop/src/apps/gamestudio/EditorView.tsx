import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText,
  RefreshCw,
  Play,
  LogOut,
  Send,
  Loader2,
  AlertCircle,
  Check,
  Sparkles,
} from "lucide-react";
import { streamTaosAgentChat } from "../appstudio/stream-chat";
import { FileEditor } from "./FileEditor";
import { parseFileBlocks, type ParsedFile } from "./file-blocks";
import { buildEditSystemPrompt, isVendorFile } from "./game-authoring-prompt";
import { getGame, gamePreviewUrl, saveGame } from "./games-api";
import type { ChatTurn, GameMeta } from "./types";

/* ------------------------------------------------------------------ */
/*  EditorView -- the core of Game Studio                               */
/*                                                                     */
/*  Three panes: a file list + textarea code editor, a live preview      */
/*  iframe pointed at the real /api/games/{id}/preview/index.html, and   */
/*  an AI chat sidebar that proposes file changes using the same         */
/*  file-block convention as Create (game-authoring-prompt.ts). Manual   */
/*  edits and applied AI changes both save through PUT /api/games/{id}   */
/*  and refresh the preview. Play maximizes the preview to fullscreen.   */
/* ------------------------------------------------------------------ */

export interface EditorViewProps {
  gameId: string;
}

const SAVE_DEBOUNCE_MS = 800;

interface PendingEdit {
  files: Record<string, ParsedFile>;
  /** files present in the current set but never touched by this proposal */
}

export function EditorView({ gameId }: EditorViewProps) {
  const [meta, setMeta] = useState<GameMeta | null>(null);
  const [files, setFiles] = useState<Record<string, string>>({});
  const [activePath, setActivePath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [previewNonce, setPreviewNonce] = useState(0);

  const [playMode, setPlayMode] = useState(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [pendingEdit, setPendingEdit] = useState<PendingEdit | null>(null);
  const [applying, setApplying] = useState(false);

  const filesRef = useRef(files);
  filesRef.current = files;
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  /* ── load the game ───────────────────────────────────────────── */

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setChat([]);
    setPendingEdit(null);
    getGame(gameId)
      .then((game) => {
        if (cancelled) return;
        setMeta({
          id: game.id,
          name: game.name,
          prompt: game.prompt,
          template: game.template,
          created: game.created,
          updated: game.updated,
        });
        setFiles(game.files);
        const preferred = ["game.js", "index.html"].find((n) => n in game.files);
        setActivePath(preferred ?? Object.keys(game.files)[0] ?? null);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  /* ── save (debounced) + preview refresh ─────────────────────────── */

  const flushSave = useCallback(
    async (nextFiles: Record<string, string>) => {
      setSaveState("saving");
      setSaveError(null);
      try {
        await saveGame(gameId, { files: nextFiles });
        if (!mountedRef.current) return;
        setSaveState("saved");
        setPreviewNonce((n) => n + 1);
      } catch (e) {
        if (!mountedRef.current) return;
        setSaveState("error");
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    },
    [gameId],
  );

  const scheduleSave = useCallback(
    (nextFiles: Record<string, string>) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        void flushSave(nextFiles);
      }, SAVE_DEBOUNCE_MS);
    },
    [flushSave],
  );

  const handleFileChange = useCallback(
    (path: string, content: string) => {
      setFiles((prev) => {
        const next = { ...prev, [path]: content };
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave],
  );

  const handleManualRefresh = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    void flushSave(filesRef.current);
  }, [flushSave]);

  /* ── fullscreen play mode ───────────────────────────────────────── */

  useEffect(() => {
    // If the user leaves native fullscreen by some means other than the
    // Exit/Escape handlers below (e.g. an OS/browser fullscreen shortcut),
    // fall out of play mode too instead of leaving the CSS-fixed overlay
    // showing while no longer actually fullscreen.
    const onChange = () => {
      if (document.fullscreenElement !== stageRef.current) setPlayMode(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const enterPlay = useCallback(() => {
    setPlayMode(true);
    requestAnimationFrame(() => {
      stageRef.current?.requestFullscreen?.().catch(() => {});
      iframeRef.current?.focus();
    });
  }, []);

  const exitPlay = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    setPlayMode(false);
  }, []);

  useEffect(() => {
    if (!playMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") exitPlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playMode, exitPlay]);

  /* ── AI chat ─────────────────────────────────────────────────────── */

  const handleSend = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    setChatInput("");
    setChatError(null);
    setChat((prev) => [...prev, { role: "user", content: text }]);
    setChatBusy(true);

    const systemPrompt = buildEditSystemPrompt(filesRef.current);
    let raw = "";
    let streamErr: string | null = null;
    try {
      await streamTaosAgentChat(
        [
          { role: "system", content: systemPrompt },
          { role: "user", content: text },
        ],
        (delta) => {
          raw += delta;
        },
        (message) => {
          streamErr = message;
        },
      );
    } catch (e) {
      streamErr = e instanceof Error ? e.message : String(e);
    }

    if (!mountedRef.current) return;
    if (streamErr) {
      setChatError(streamErr);
      setChatBusy(false);
      return;
    }

    const parsed = parseFileBlocks(raw).filter((f) => !isVendorFile(f.path));
    if (parsed.length === 0) {
      setChat((prev) => [
        ...prev,
        { role: "assistant", content: raw.trim() || "No file changes were proposed." },
      ]);
    } else {
      const byPath: Record<string, ParsedFile> = {};
      for (const f of parsed) byPath[f.path] = f;
      setPendingEdit({ files: byPath });
      const summary = Object.keys(byPath)
        .map((p) => (p in filesRef.current ? `updated ${p}` : `new file ${p}`))
        .join(", ");
      setChat((prev) => [
        ...prev,
        { role: "assistant", content: `Proposed changes: ${summary}.` },
      ]);
    }
    setChatBusy(false);
  }, [chatInput, chatBusy]);

  const handleApply = useCallback(() => {
    if (!pendingEdit) return;
    setApplying(true);
    setFiles((prev) => {
      const next = { ...prev };
      for (const [path, f] of Object.entries(pendingEdit.files)) {
        next[path] = f.content;
      }
      void flushSave(next);
      return next;
    });
    setPendingEdit(null);
    setApplying(false);
  }, [pendingEdit, flushSave]);

  /* ── render ─────────────────────────────────────────────────────── */

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
        <Loader2 size={16} className="mr-2 animate-spin" />
        Loading game...
      </div>
    );
  }

  if (loadError || !meta) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-red-400">
        <AlertCircle size={16} />
        {loadError ?? "Game not found"}
      </div>
    );
  }

  const paths = Object.keys(files);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="truncate text-[17px] font-bold tracking-[-0.02em]">{meta.name}</h2>
        <span className="flex-none text-[12px] text-shell-text-tertiary">
          {saveState === "saving" && "Saving..."}
          {saveState === "saved" && "Saved"}
          {saveState === "error" && (
            <span className="flex items-center gap-1 text-red-400">
              <AlertCircle size={12} /> {saveError}
            </span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={handleManualRefresh}
            aria-label="Refresh preview"
            className="flex h-8 items-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface px-2.5 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active"
          >
            <RefreshCw size={13} />
            Refresh
          </button>
          <button
            type="button"
            onClick={enterPlay}
            className="flex h-8 items-center gap-1.5 rounded-lg bg-gradient-to-br from-accent to-accent/70 px-3 text-[12px] font-bold text-white"
          >
            <Play size={13} />
            Play
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[200px_1fr_300px]">
        {/* file list */}
        <div className="flex min-h-0 flex-col overflow-auto border-r border-shell-border bg-shell-bg-deep px-2 py-2.5">
          <div className="px-2 pb-2 pt-1 text-[10.5px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Files
          </div>
          {paths.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setActivePath(p)}
              aria-current={activePath === p ? "true" : undefined}
              className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-[5px] text-left text-[12.5px] ${
                activePath === p
                  ? "bg-shell-surface-active text-shell-text"
                  : "text-shell-text-secondary hover:bg-shell-surface"
              }`}
            >
              <FileText size={13} className="flex-none text-shell-text-tertiary" />
              <span className="truncate">{p}</span>
            </button>
          ))}
        </div>

        {/* editor + preview */}
        <div className="grid min-h-0 min-w-0 grid-rows-1 lg:grid-cols-2">
          <div className="min-h-0 min-w-0 border-b border-shell-border bg-[#1a1b2e] lg:border-b-0 lg:border-r">
            {activePath ? (
              <FileEditor
                path={activePath}
                content={files[activePath] ?? ""}
                onChange={(v) => handleFileChange(activePath, v)}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-[12px] text-shell-text-tertiary">
                No files
              </div>
            )}
          </div>

          <div
            ref={stageRef}
            className="relative min-h-0 min-w-0 bg-[#0a0d14]"
            style={playMode ? { position: "fixed", inset: 0, zIndex: 60 } : undefined}
          >
            <iframe
              ref={iframeRef}
              key={previewNonce}
              src={`${gamePreviewUrl(gameId, "index.html")}?v=${previewNonce}`}
              sandbox="allow-scripts"
              title="Game preview"
              className="h-full w-full border-0"
            />
            {playMode && (
              <button
                type="button"
                onClick={exitPlay}
                aria-label="Exit to taOS"
                className="absolute z-50 inline-flex items-center gap-1.5 rounded-full border border-accent/45 bg-[rgba(16,18,24,0.62)] px-3 py-1.5 text-[12px] font-bold text-white shadow-lg backdrop-blur-md transition-all hover:-translate-y-0.5 hover:bg-[rgba(28,32,42,0.82)]"
                style={{
                  top: "max(12px, env(safe-area-inset-top, 12px))",
                  left: "max(12px, env(safe-area-inset-left, 12px))",
                }}
              >
                <LogOut size={15} className="-scale-x-100" />
                Exit to taOS
                <span className="ml-0.5 rounded border border-white/25 px-1.5 py-px text-[9.5px] font-bold tracking-wide text-white/60">
                  ESC
                </span>
              </button>
            )}
          </div>
        </div>

        {/* AI chat sidebar */}
        <div className="flex min-h-0 flex-col border-l border-shell-border">
          <div className="flex items-center gap-2 border-b border-shell-border px-3.5 py-3">
            <Sparkles size={15} className="text-accent" />
            <h3 className="text-[13px] font-bold">Ask the agent</h3>
          </div>

          <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
            {chat.length === 0 && (
              <p className="px-1 py-2 text-[12px] text-shell-text-tertiary">
                Ask for a change (e.g. "make the player faster" or "add a double jump") and review
                the proposed files before applying them.
              </p>
            )}
            {chat.map((turn, i) => (
              <div
                key={i}
                className={`mb-2 rounded-xl px-3 py-2 text-[12.5px] leading-relaxed ${
                  turn.role === "user"
                    ? "bg-accent-soft text-shell-text"
                    : "bg-shell-surface text-shell-text-secondary"
                }`}
              >
                {turn.content}
              </div>
            ))}
            {pendingEdit && (
              <div className="mb-2 rounded-xl border border-accent/30 bg-accent-soft px-3 py-2.5">
                <div className="mb-1.5 text-[11.5px] font-bold uppercase tracking-wide text-shell-text-secondary">
                  Changed files
                </div>
                <ul className="mb-2 flex flex-col gap-1 text-[12px] text-shell-text-secondary">
                  {Object.keys(pendingEdit.files).map((p) => {
                    const before = files[p]?.split("\n").length ?? 0;
                    const after = pendingEdit.files[p]!.content.split("\n").length;
                    return (
                      <li key={p} className="flex items-center gap-1.5">
                        <FileText size={12} className="flex-none" />
                        <span className="truncate font-semibold">{p}</span>
                        <span className="text-shell-text-tertiary">
                          {p in files ? `${before} → ${after} lines` : `new, ${after} lines`}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <button
                  type="button"
                  onClick={handleApply}
                  disabled={applying}
                  className="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12px] font-bold text-white disabled:opacity-60"
                >
                  <Check size={13} />
                  Apply
                </button>
              </div>
            )}
            {chatError && (
              <div role="alert" className="mb-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
                {chatError}
              </div>
            )}
            {chatBusy && (
              <div className="flex items-center gap-2 px-1 py-2 text-[12px] text-shell-text-tertiary">
                <Loader2 size={14} className="animate-spin" />
                Thinking...
              </div>
            )}
          </div>

          <div className="flex flex-none items-center gap-2 border-t border-shell-border p-2.5">
            <label htmlFor="gs-chat-input" className="sr-only">
              Ask the agent for a change
            </label>
            <textarea
              id="gs-chat-input"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              rows={2}
              disabled={chatBusy}
              placeholder="Describe a change..."
              className="min-h-[40px] flex-1 resize-none rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20 disabled:opacity-60"
            />
            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={chatBusy || !chatInput.trim()}
              aria-label="Send"
              className="flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-accent text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {chatBusy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
