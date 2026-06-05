/**
 * StreamedBrowserApp — always-on streamed Chromium session with session switcher (C2).
 *
 * Session switcher (slim left rail) lists:
 *   • "My browser" — the user's own always-on session via GET /api/browser/sessions/mine
 *   • Agent sessions — from GET /api/browser/sessions, polled every ~10 s
 *
 * Selecting "My browser" uses the existing /mine flow (C1 behaviour).
 * Selecting an agent session fetches GET /api/browser/sessions/{id} for its stream_token,
 * then renders LiveBrowserView in watch-only mode with a "watching <agent>'s browser" label.
 *
 * Request-control button is rendered disabled — Neko member-control handoff (sub-plan G)
 * is not yet built. The button is a placeholder so the UX slot is reserved.
 *
 * Migrating state (status === "migrating", emitted by sub-plan F) is rendered like the
 * connecting state with the message "Moving your browser to another device…".
 *
 * No browser chrome is built here — Chromium's omnibox/tabs live inside the stream.
 *
 * State machine (per selected session):
 *   loading      — initial fetch in-flight
 *   connecting   — session not yet running; polling
 *   migrating    — session is migrating to another node
 *   live         — running + neko_url + stream_token present
 *   no_node      — 409 no_capable_node (user's own session only)
 *   error        — any other failure (shows Retry)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { LiveBrowserView } from "@/apps/BrowserApp/LiveBrowserView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { Loader2, MonitorPlay, AlertCircle, Monitor, Bot } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_TRIES = 20;
const SESSION_LIST_POLL_MS = 10_000;

// ─── Types ────────────────────────────────────────────────────────────────────

interface SessionSummary {
  id: string;
  owner_type: "user" | "agent";
  owner_id: string;
  status: string;
  neko_url: string | null;
  url: string | null;
}

interface BrowserSession extends SessionSummary {
  stream_token?: string | null;
}

type Selection =
  | { kind: "mine" }
  | { kind: "agent"; sessionId: string; agentName: string };

type ViewState =
  | { phase: "loading" }
  | { phase: "connecting" }
  | { phase: "migrating" }
  | { phase: "live"; nekoUrl: string; streamToken: string; watchLabel?: string }
  | { phase: "no_node" }
  | { phase: "error"; message: string };

interface StreamedBrowserAppProps {
  windowId: string;
}

// ─── Session Switcher ─────────────────────────────────────────────────────────

interface SwitcherProps {
  sessions: SessionSummary[];
  selected: Selection;
  onSelect: (sel: Selection) => void;
  isMobile: boolean;
}

function SessionSwitcher({ sessions, selected, onSelect, isMobile }: SwitcherProps) {
  const agentSessions = sessions.filter((s) => s.owner_type === "agent");
  const isMineSel = selected.kind === "mine";

  // Mobile: collapse the sidebar to a thin horizontal selector, and hide it
  // entirely when there's only the user's own browser so the stream is full-bleed.
  if (isMobile) {
    if (agentSessions.length === 0) return null;
    return (
      <nav
        aria-label="Browser sessions"
        className="flex flex-row gap-1.5 shrink-0 border-b border-shell-border-subtle bg-shell-surface px-2 py-1.5 overflow-x-auto"
      >
        <button
          type="button"
          aria-current={isMineSel ? "true" : undefined}
          onClick={() => onSelect({ kind: "mine" })}
          className={[
            "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs whitespace-nowrap shrink-0 transition-colors",
            isMineSel ? "bg-white/[0.12] text-shell-text" : "text-shell-text-secondary hover:bg-white/[0.06]",
          ].join(" ")}
        >
          <Monitor size={13} aria-hidden="true" />
          <span>My browser</span>
        </button>
        {agentSessions.map((s) => {
          const isSel = selected.kind === "agent" && selected.sessionId === s.id;
          return (
            <button
              key={s.id}
              type="button"
              aria-current={isSel ? "true" : undefined}
              onClick={() => onSelect({ kind: "agent", sessionId: s.id, agentName: s.owner_id })}
              className={[
                "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs whitespace-nowrap shrink-0 transition-colors",
                isSel ? "bg-white/[0.12] text-shell-text" : "text-shell-text-secondary hover:bg-white/[0.06]",
              ].join(" ")}
            >
              <Bot size={13} aria-hidden="true" />
              <span className="max-w-[120px] truncate">{s.owner_id}</span>
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <nav
      aria-label="Browser sessions"
      className="flex flex-col gap-0.5 w-44 shrink-0 border-r border-shell-border-subtle bg-shell-surface px-1.5 py-2 overflow-y-auto"
    >
      {/* My browser */}
      <button
        type="button"
        aria-current={isMineSel ? "true" : undefined}
        onClick={() => onSelect({ kind: "mine" })}
        className={[
          "flex items-center gap-2 rounded px-2 py-1.5 text-xs text-left w-full transition-colors",
          isMineSel
            ? "bg-white/[0.1] text-shell-text"
            : "text-shell-text-secondary hover:bg-white/[0.06] hover:text-shell-text",
        ].join(" ")}
      >
        <Monitor size={13} aria-hidden="true" className="shrink-0" />
        <span className="truncate">My browser</span>
      </button>

      {agentSessions.length > 0 && (
        <>
          <p className="px-2 pt-2 pb-0.5 text-[10px] font-medium uppercase tracking-wide text-shell-text-secondary/60">
            Agents
          </p>
          {agentSessions.map((s) => {
            const isSel = selected.kind === "agent" && selected.sessionId === s.id;
            const label = s.owner_id;
            const sublabel = s.url ? truncateUrl(s.url) : s.status;
            return (
              <button
                key={s.id}
                type="button"
                aria-current={isSel ? "true" : undefined}
                onClick={() => onSelect({ kind: "agent", sessionId: s.id, agentName: s.owner_id })}
                className={[
                  "flex items-center gap-2 rounded px-2 py-1.5 text-xs text-left w-full transition-colors",
                  isSel
                    ? "bg-white/[0.1] text-shell-text"
                    : "text-shell-text-secondary hover:bg-white/[0.06] hover:text-shell-text",
                ].join(" ")}
              >
                <Bot size={13} aria-hidden="true" className="shrink-0" />
                <div className="min-w-0">
                  <div className="truncate">{label}</div>
                  {sublabel && (
                    <div className="truncate text-[10px] opacity-60">{sublabel}</div>
                  )}
                </div>
              </button>
            );
          })}
        </>
      )}
    </nav>
  );
}

function truncateUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname + (u.pathname !== "/" ? u.pathname.slice(0, 20) : "");
  } catch {
    return url.slice(0, 24);
  }
}

// ─── Main App ─────────────────────────────────────────────────────────────────

export function StreamedBrowserApp({ windowId: _windowId }: StreamedBrowserAppProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<Selection>({ kind: "mine" });
  const [viewState, setViewState] = useState<ViewState>({ phase: "loading" });

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triesRef = useRef(0);
  const cancelledRef = useRef(false);
  const listTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // ── Session list poller ──────────────────────────────────────────────────

  const fetchSessionList = useCallback(async () => {
    try {
      const resp = await fetch("/api/browser/sessions", { credentials: "include" });
      if (!resp.ok) return;
      const data: { sessions: SessionSummary[] } = await resp.json();
      setSessions(data.sessions ?? []);
    } catch {
      // Non-fatal: list is best-effort; current view keeps working
    }
  }, []);

  useEffect(() => {
    void fetchSessionList();
    listTimerRef.current = setInterval(() => void fetchSessionList(), SESSION_LIST_POLL_MS);
    return () => {
      if (listTimerRef.current) clearInterval(listTimerRef.current);
    };
  }, [fetchSessionList]);

  // ── My browser flow (C1) ─────────────────────────────────────────────────

  const fetchMine = useCallback(async (isRetry = false) => {
    cancelledRef.current = false;
    triesRef.current = 0;
    stopPolling();

    if (!isRetry) setViewState({ phase: "loading" });

    let resp: Response;
    try {
      resp = await fetch("/api/browser/sessions/mine", { credentials: "include" });
    } catch {
      if (!cancelledRef.current) {
        setViewState({ phase: "error", message: "Could not reach the taOS server." });
      }
      return;
    }

    if (cancelledRef.current) return;

    if (resp.status === 409) {
      let body: { error?: string } = {};
      try { body = await resp.json(); } catch { /* ignore */ }
      if (body.error === "no_capable_node") {
        setViewState({ phase: "no_node" });
      } else {
        setViewState({ phase: "error", message: `Unexpected conflict (${body.error ?? resp.status}).` });
      }
      return;
    }

    if (!resp.ok) {
      setViewState({ phase: "error", message: `Server error (${resp.status}).` });
      return;
    }

    let session: BrowserSession;
    try {
      session = await resp.json();
    } catch {
      setViewState({ phase: "error", message: "Could not parse server response." });
      return;
    }

    if (session.status === "migrating") {
      setViewState({ phase: "migrating" });
      schedulePoll(session.id);
      return;
    }

    if (session.status === "running" && session.neko_url && session.stream_token) {
      setViewState({ phase: "live", nekoUrl: session.neko_url, streamToken: session.stream_token });
      return;
    }

    setViewState({ phase: "connecting" });
    schedulePoll(session.id);
  }, [stopPolling]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Agent session flow ───────────────────────────────────────────────────

  const fetchAgentSession = useCallback(async (sessionId: string, agentName: string, isRetry = false) => {
    cancelledRef.current = false;
    triesRef.current = 0;
    stopPolling();

    if (!isRetry) setViewState({ phase: "loading" });

    let resp: Response;
    try {
      resp = await fetch(`/api/browser/sessions/${encodeURIComponent(sessionId)}`, {
        credentials: "include",
      });
    } catch {
      if (!cancelledRef.current) {
        setViewState({ phase: "error", message: "Could not reach the taOS server." });
      }
      return;
    }

    if (cancelledRef.current) return;

    if (!resp.ok) {
      setViewState({ phase: "error", message: `Could not load agent session (${resp.status}).` });
      return;
    }

    let session: BrowserSession;
    try {
      session = await resp.json();
    } catch {
      setViewState({ phase: "error", message: "Could not parse session response." });
      return;
    }

    if (session.status === "migrating") {
      setViewState({ phase: "migrating" });
      schedulePoll(sessionId, agentName);
      return;
    }

    if (session.status === "running" && session.neko_url && session.stream_token) {
      setViewState({
        phase: "live",
        nekoUrl: session.neko_url,
        streamToken: session.stream_token,
        watchLabel: agentName,
      });
      return;
    }

    // Session exists but not yet running
    setViewState({ phase: "connecting" });
    schedulePoll(sessionId, agentName);
  }, [stopPolling]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Poll loop (shared) ───────────────────────────────────────────────────

  const schedulePoll = useCallback((sessionId: string, agentName?: string) => {
    if (cancelledRef.current) return;
    triesRef.current += 1;
    if (triesRef.current > POLL_MAX_TRIES) {
      setViewState({ phase: "error", message: "Browser session took too long to start." });
      return;
    }

    pollRef.current = setTimeout(async () => {
      if (cancelledRef.current) return;

      let resp: Response;
      try {
        resp = await fetch(`/api/browser/sessions/${encodeURIComponent(sessionId)}`, {
          credentials: "include",
        });
      } catch {
        if (!cancelledRef.current) {
          setViewState({ phase: "error", message: "Lost connection while waiting for browser to start." });
        }
        return;
      }

      if (cancelledRef.current) return;

      if (!resp.ok) {
        setViewState({ phase: "error", message: `Session poll failed (${resp.status}).` });
        return;
      }

      let session: BrowserSession;
      try {
        session = await resp.json();
      } catch {
        setViewState({ phase: "error", message: "Could not parse session response." });
        return;
      }

      if (session.status === "migrating") {
        setViewState({ phase: "migrating" });
        schedulePoll(sessionId, agentName);
        return;
      }

      if (session.status === "running" && session.neko_url && session.stream_token) {
        setViewState({
          phase: "live",
          nekoUrl: session.neko_url,
          streamToken: session.stream_token,
          watchLabel: agentName,
        });
      } else {
        schedulePoll(sessionId, agentName);
      }
    }, POLL_INTERVAL_MS);
  }, []); // stable

  // ── Effect: re-fetch when selection changes ──────────────────────────────

  useEffect(() => {
    cancelledRef.current = false;
    if (selected.kind === "mine") {
      void fetchMine();
    } else {
      void fetchAgentSession(selected.sessionId, selected.agentName);
    }
    return () => {
      cancelledRef.current = true;
      stopPolling();
    };
  }, [selected, fetchMine, fetchAgentSession, stopPolling]);

  // ── Retry handler ────────────────────────────────────────────────────────

  const handleRetry = useCallback(() => {
    if (selected.kind === "mine") {
      void fetchMine(true);
    } else {
      void fetchAgentSession(selected.sessionId, selected.agentName, true);
    }
  }, [selected, fetchMine, fetchAgentSession]);

  const isMobile = useIsMobile();

  // ── Render ───────────────────────────────────────────────────────────────

  const renderContent = () => {
    if (viewState.phase === "live") {
      return (
        <div className="relative flex-1 min-w-0 min-h-0">
          <LiveBrowserView nekoUrl={viewState.nekoUrl} streamToken={viewState.streamToken} />
          {viewState.watchLabel && (
            <div className="absolute top-2 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-black/60 text-white text-xs px-3 py-1 rounded-full pointer-events-none">
              <Bot size={12} aria-hidden="true" />
              <span>Watching {viewState.watchLabel}&apos;s browser</span>
              {/* Request-control — Neko member-control handoff lands in a later sub-plan */}
              <Tooltip.Provider delayDuration={200}>
                <Tooltip.Root>
                  <Tooltip.Trigger asChild>
                    <button
                      type="button"
                      aria-label="Request control (coming soon)"
                      disabled
                      className="pointer-events-auto ml-1 px-2 py-0.5 rounded bg-white/20 text-white/60 text-[10px] font-medium cursor-not-allowed"
                    >
                      Request control
                    </button>
                  </Tooltip.Trigger>
                  <Tooltip.Portal>
                    <Tooltip.Content
                      side="bottom"
                      className="bg-black/80 text-white text-xs px-2 py-1 rounded shadow-lg"
                    >
                      Coming soon
                      <Tooltip.Arrow className="fill-black/80" />
                    </Tooltip.Content>
                  </Tooltip.Portal>
                </Tooltip.Root>
              </Tooltip.Provider>
            </div>
          )}
        </div>
      );
    }

    if (viewState.phase === "loading" || viewState.phase === "connecting" || viewState.phase === "migrating") {
      const message =
        viewState.phase === "loading"
          ? "Starting your browser…"
          : viewState.phase === "migrating"
          ? "Moving your browser to another device…"
          : "Waiting for browser to be ready…";

      return (
        <div
          role="status"
          aria-label={message}
          className="flex flex-1 flex-col items-center justify-center gap-3 text-shell-text-secondary bg-shell-bg"
        >
          <Loader2 size={28} className="animate-spin" aria-hidden="true" />
          <span className="text-sm">{message}</span>
        </div>
      );
    }

    if (viewState.phase === "no_node") {
      return (
        <div
          role="alert"
          className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center bg-shell-bg"
        >
          <MonitorPlay size={40} className="text-shell-text-secondary" aria-hidden="true" />
          <p className="text-sm font-medium text-shell-text">
            This device can&apos;t run the browser yet
          </p>
          <p className="text-xs text-shell-text-secondary max-w-xs">
            Add a capable device to your cluster and the streamed browser will be available automatically.
          </p>
        </div>
      );
    }

    // error phase
    return (
      <div
        role="alert"
        className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center bg-shell-bg"
      >
        <AlertCircle size={32} className="text-shell-text-secondary" aria-hidden="true" />
        <p className="text-sm text-shell-text-secondary">{viewState.message}</p>
        <button
          type="button"
          onClick={handleRetry}
          className="px-4 py-1.5 text-xs rounded bg-shell-surface border border-shell-border-subtle hover:bg-shell-hover"
        >
          Retry
        </button>
      </div>
    );
  };

  return (
    <div className={`flex ${isMobile ? "flex-col" : "flex-row"} w-full h-full overflow-hidden bg-shell-bg`}>
      <SessionSwitcher sessions={sessions} selected={selected} onSelect={setSelected} isMobile={isMobile} />
      {renderContent()}
    </div>
  );
}
