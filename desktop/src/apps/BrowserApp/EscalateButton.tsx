/**
 * EscalateButton — "Open in full browser" control + live session lifecycle.
 *
 * Extracted as a standalone component so it's independently testable without
 * mounting the full BrowserApp tree.
 *
 * State machine (local only — no store changes):
 *   idle → starting (POST 201) → polling → live  (render LiveBrowserView)
 *   idle → no_node             (POST 409)         (render gate banner)
 *
 * Polling: every ~1.5 s, cap 20 tries (~30 s).
 */
import { useState, useRef, useCallback } from "react";
import { MonitorPlay } from "lucide-react";
import { LiveBrowserView } from "./LiveBrowserView";

interface BrowserSession {
  id: string;
  status: string;
  neko_url: string | null;
  stream_token?: string | null;
}

type EscalateState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "polling"; sessionId: string }
  | { phase: "live"; nekoUrl: string; streamToken: string }
  | { phase: "no_node" };

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_TRIES = 20;

interface EscalateButtonProps {
  /** The URL of the current tab — sent as the body of POST /api/browser/sessions */
  tabUrl: string;
}

export function EscalateButton({ tabUrl }: EscalateButtonProps) {
  const [state, setState] = useState<EscalateState>({ phase: "idle" });
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triesRef = useRef(0);
  const cancelledRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const poll = useCallback((sessionId: string) => {
    if (cancelledRef.current) return;
    triesRef.current += 1;
    if (triesRef.current > POLL_MAX_TRIES) {
      setState({ phase: "idle" });
      return;
    }

    fetch(`/api/browser/sessions/${encodeURIComponent(sessionId)}`, {
      credentials: "include",
    })
      .then(async (resp) => {
        if (cancelledRef.current) return;
        if (!resp.ok) {
          setState({ phase: "idle" });
          return;
        }
        const session: BrowserSession = await resp.json();
        if (session.status === "running" && session.neko_url && session.stream_token) {
          setState({
            phase: "live",
            nekoUrl: session.neko_url,
            streamToken: session.stream_token,
          });
        } else {
          // Still pending — schedule next poll
          pollRef.current = setTimeout(() => poll(sessionId), POLL_INTERVAL_MS);
        }
      })
      .catch(() => {
        if (!cancelledRef.current) setState({ phase: "idle" });
      });
  }, []);

  const handleEscalate = useCallback(async () => {
    if (state.phase !== "idle") return;
    cancelledRef.current = false;
    triesRef.current = 0;
    setState({ phase: "starting" });

    let resp: Response;
    try {
      resp = await fetch("/api/browser/sessions", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: tabUrl }),
      });
    } catch {
      setState({ phase: "idle" });
      return;
    }

    if (resp.status === 409) {
      let body: { error?: string } = {};
      try { body = await resp.json(); } catch { /* ignore */ }
      if (body.error === "no_capable_node") {
        setState({ phase: "no_node" });
      } else {
        setState({ phase: "idle" });
      }
      return;
    }

    if (!resp.ok) {
      setState({ phase: "idle" });
      return;
    }

    let session: BrowserSession;
    try {
      const body = await resp.json();
      session = body.session ?? body;
    } catch {
      setState({ phase: "idle" });
      return;
    }

    // If already running (fast path), go live immediately
    if (session.status === "running" && session.neko_url && session.stream_token) {
      setState({ phase: "live", nekoUrl: session.neko_url, streamToken: session.stream_token });
      return;
    }

    setState({ phase: "polling", sessionId: session.id });
    poll(session.id);
  }, [state.phase, tabUrl, poll]);

  // If live, render the browser view filling the parent
  if (state.phase === "live") {
    return (
      <LiveBrowserView nekoUrl={state.nekoUrl} streamToken={state.streamToken} />
    );
  }

  return (
    <>
      {/* The trigger button — always present so the toolbar keeps its shape */}
      <button
        type="button"
        aria-label="Open in full browser"
        disabled={state.phase !== "idle"}
        onClick={handleEscalate}
        className="p-1 rounded hover:bg-shell-hover disabled:opacity-40 disabled:cursor-not-allowed"
        title="Open in full browser"
      >
        <MonitorPlay size={16} />
      </button>

      {/* Starting / polling indicator */}
      {(state.phase === "starting" || state.phase === "polling") && (
        <span className="text-xs text-shell-text-secondary px-1">
          Starting full browser…
        </span>
      )}

      {/* No-node gate banner */}
      {state.phase === "no_node" && (
        <div
          role="alert"
          className="absolute top-full left-0 right-0 z-50 flex items-start gap-2 px-3 py-2 bg-shell-surface border border-shell-border-subtle text-shell-text-secondary text-xs shadow-md"
        >
          <span className="flex-1">
            A full browser needs a more capable device on your taOS. Add one to enable this.
          </span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => {
              stopPolling();
              cancelledRef.current = true;
              setState({ phase: "idle" });
            }}
            className="shrink-0 p-0.5 rounded hover:bg-shell-hover"
          >
            ✕
          </button>
        </div>
      )}
    </>
  );
}
