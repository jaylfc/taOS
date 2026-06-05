/**
 * StreamedBrowserApp — always-on streamed Chromium session (sub-plan C1).
 *
 * Calls GET /api/browser/sessions/mine which both returns and (if needed)
 * starts the user's personal browser session. When running with neko_url
 * and stream_token, renders the live stream full-bleed via LiveBrowserView.
 *
 * No browser chrome built here — Chromium's own omnibox/tabs live inside
 * the stream.
 *
 * State machine:
 *   loading (first fetch in-flight)
 *   → connecting (200 but status not yet "running") — poll until running
 *   → live        (running + neko_url + stream_token present)
 *   → no_node     (409 no_capable_node — gate-and-guide message)
 *   → error       (any other failure — shows Retry button)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { LiveBrowserView } from "@/apps/BrowserApp/LiveBrowserView";
import { Loader2, MonitorPlay, AlertCircle } from "lucide-react";

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_TRIES = 20;

interface BrowserSession {
  id: string;
  status: string;
  neko_url: string | null;
  stream_token?: string | null;
}

type AppState =
  | { phase: "loading" }
  | { phase: "connecting" }
  | { phase: "live"; nekoUrl: string; streamToken: string }
  | { phase: "no_node" }
  | { phase: "error"; message: string };

interface StreamedBrowserAppProps {
  windowId: string;
}

export function StreamedBrowserApp({ windowId: _windowId }: StreamedBrowserAppProps) {
  const [state, setState] = useState<AppState>({ phase: "loading" });
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triesRef = useRef(0);
  const cancelledRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchMine = useCallback(async (isRetry = false) => {
    cancelledRef.current = false;
    triesRef.current = 0;
    stopPolling();

    if (!isRetry) {
      setState({ phase: "loading" });
    }

    let resp: Response;
    try {
      resp = await fetch("/api/browser/sessions/mine", {
        credentials: "include",
      });
    } catch {
      if (!cancelledRef.current) {
        setState({ phase: "error", message: "Could not reach the taOS server." });
      }
      return;
    }

    if (cancelledRef.current) return;

    if (resp.status === 409) {
      let body: { error?: string } = {};
      try {
        body = await resp.json();
      } catch { /* ignore */ }
      if (body.error === "no_capable_node") {
        setState({ phase: "no_node" });
      } else {
        setState({ phase: "error", message: `Unexpected conflict (${body.error ?? resp.status}).` });
      }
      return;
    }

    if (!resp.ok) {
      setState({ phase: "error", message: `Server error (${resp.status}).` });
      return;
    }

    let session: BrowserSession;
    try {
      session = await resp.json();
    } catch {
      setState({ phase: "error", message: "Could not parse server response." });
      return;
    }

    if (session.status === "running" && session.neko_url && session.stream_token) {
      setState({ phase: "live", nekoUrl: session.neko_url, streamToken: session.stream_token });
      return;
    }

    // Not yet running — enter polling loop
    setState({ phase: "connecting" });
    schedulePoll(session.id);
  }, [stopPolling]); // eslint-disable-line react-hooks/exhaustive-deps

  const schedulePoll = useCallback((sessionId: string) => {
    if (cancelledRef.current) return;
    triesRef.current += 1;
    if (triesRef.current > POLL_MAX_TRIES) {
      setState({ phase: "error", message: "Browser session took too long to start." });
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
          setState({ phase: "error", message: "Lost connection while waiting for browser to start." });
        }
        return;
      }

      if (cancelledRef.current) return;

      if (!resp.ok) {
        setState({ phase: "error", message: `Session poll failed (${resp.status}).` });
        return;
      }

      let session: BrowserSession;
      try {
        session = await resp.json();
      } catch {
        setState({ phase: "error", message: "Could not parse session response." });
        return;
      }

      if (session.status === "running" && session.neko_url && session.stream_token) {
        setState({ phase: "live", nekoUrl: session.neko_url, streamToken: session.stream_token });
      } else {
        schedulePoll(sessionId);
      }
    }, POLL_INTERVAL_MS);
  }, []); // stable — no deps needed

  useEffect(() => {
    void fetchMine();
    return () => {
      cancelledRef.current = true;
      stopPolling();
    };
  }, [fetchMine, stopPolling]);

  if (state.phase === "live") {
    return (
      <div style={{ width: "100%", height: "100%" }}>
        <LiveBrowserView nekoUrl={state.nekoUrl} streamToken={state.streamToken} />
      </div>
    );
  }

  if (state.phase === "loading" || state.phase === "connecting") {
    return (
      <div
        role="status"
        aria-label="Starting browser"
        className="flex flex-col items-center justify-center h-full gap-3 text-shell-text-secondary bg-shell-bg"
      >
        <Loader2 size={28} className="animate-spin" aria-hidden="true" />
        <span className="text-sm">
          {state.phase === "loading" ? "Starting your browser…" : "Waiting for browser to be ready…"}
        </span>
      </div>
    );
  }

  if (state.phase === "no_node") {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center h-full gap-4 px-8 text-center bg-shell-bg"
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
      className="flex flex-col items-center justify-center h-full gap-4 px-8 text-center bg-shell-bg"
    >
      <AlertCircle size={32} className="text-shell-text-secondary" aria-hidden="true" />
      <p className="text-sm text-shell-text-secondary">
        {state.message}
      </p>
      <button
        type="button"
        onClick={() => void fetchMine(true)}
        className="px-4 py-1.5 text-xs rounded bg-shell-surface border border-shell-border-subtle hover:bg-shell-hover"
      >
        Retry
      </button>
    </div>
  );
}
