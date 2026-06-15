/**
 * BrowserModeToggle - Proxy / Streamed segmented control for the active tab.
 *
 * Two browser engines back a tab:
 *  - Proxy:    the URL-rewriting iframe browser (default). No liveSession.
 *  - Streamed: a real Chromium session streamed from the host over WebRTC
 *              (the existing Neko/liveSession path). The active tab carries a
 *              `liveSession` while streamed.
 *
 * This control surfaces that engine choice as a segmented toggle in the tab
 * strip and drives the existing escalation lifecycle:
 *  - Streamed: POST /api/browser/sessions, poll until running, then
 *              store.setTabLiveSession(...) - identical to EscalateButton.
 *  - Proxy:    store.setTabLiveSession(..., null) drops the stream and the tab
 *              falls back to the proxied iframe.
 *
 * A 409 with `no_capable_node` means the taOS has no device able to run a real
 * browser; we show a small inline gate hint, matching EscalateButton.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Globe, MonitorPlay } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";

interface BrowserModeToggleProps {
  windowId: string;
}

interface BrowserSession {
  id: string;
  status: string;
  neko_url: string | null;
  stream_token?: string | null;
}

type Phase = "idle" | "starting" | "polling" | "no_node";

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_TRIES = 20;

export function BrowserModeToggle({ windowId }: BrowserModeToggleProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const setTabLiveSession = useBrowserStore((s) => s.setTabLiveSession);

  const [phase, setPhase] = useState<Phase>("idle");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triesRef = useRef(0);
  const cancelledRef = useRef(false);

  const activeTab = win?.tabs.find((t) => t.id === win.activeTabId);
  const isStreamed = !!activeTab?.liveSession;

  // Stop any in-flight poll if this instance unmounts (tab closed, strip
  // re-rendered away). Without this the pending setTimeout keeps firing poll(),
  // which fetches and calls setPhase on a dead component.
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      if (pollRef.current) {
        clearTimeout(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  const poll = useCallback(
    (sessionId: string, tabId: string) => {
      if (cancelledRef.current) return;
      triesRef.current += 1;
      if (triesRef.current > POLL_MAX_TRIES) {
        setPhase("idle");
        return;
      }
      fetch(`/api/browser/sessions/${encodeURIComponent(sessionId)}`, {
        credentials: "include",
      })
        .then(async (resp) => {
          if (cancelledRef.current) return;
          if (!resp.ok) {
            setPhase("idle");
            return;
          }
          const session: BrowserSession = await resp.json();
          if (session.status === "running" && session.neko_url && session.stream_token) {
            setTabLiveSession(windowId, tabId, {
              nekoUrl: session.neko_url,
              streamToken: session.stream_token,
            });
            setPhase("idle");
          } else {
            pollRef.current = setTimeout(() => poll(sessionId, tabId), POLL_INTERVAL_MS);
          }
        })
        .catch(() => {
          if (!cancelledRef.current) setPhase("idle");
        });
    },
    [setTabLiveSession, windowId],
  );

  const goStreamed = useCallback(async () => {
    if (!activeTab || phase !== "idle") return;
    const tabId = activeTab.id;
    cancelledRef.current = false;
    triesRef.current = 0;
    setPhase("starting");

    let resp: Response;
    try {
      resp = await fetch("/api/browser/sessions", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: activeTab.url }),
      });
    } catch {
      setPhase("idle");
      return;
    }

    // The user may have clicked Proxy while the POST was in flight (goProxy sets
    // cancelledRef + phase=idle). Honour that cancellation instead of forcing
    // the tab back into a streamed session it no longer wants.
    if (cancelledRef.current) return;

    if (resp.status === 409) {
      let body: { error?: string } = {};
      try {
        body = await resp.json();
      } catch {
        /* ignore */
      }
      setPhase(body.error === "no_capable_node" ? "no_node" : "idle");
      return;
    }

    if (!resp.ok) {
      setPhase("idle");
      return;
    }

    let session: BrowserSession;
    try {
      const body = await resp.json();
      session = body.session ?? body;
    } catch {
      if (!cancelledRef.current) setPhase("idle");
      return;
    }

    // Re-check after the JSON-parse await as well: a Proxy click or unmount
    // during that await must still cancel before we commit the live session.
    if (cancelledRef.current) return;

    if (session.status === "running" && session.neko_url && session.stream_token) {
      setTabLiveSession(windowId, tabId, {
        nekoUrl: session.neko_url,
        streamToken: session.stream_token,
      });
      setPhase("idle");
      return;
    }

    setPhase("polling");
    poll(session.id, tabId);
  }, [activeTab, phase, poll, setTabLiveSession, windowId]);

  const goProxy = useCallback(() => {
    if (!activeTab) return;
    cancelledRef.current = true;
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    setPhase("idle");
    if (activeTab.liveSession) {
      setTabLiveSession(windowId, activeTab.id, null);
    }
  }, [activeTab, setTabLiveSession, windowId]);

  if (!activeTab) return null;

  const busy = phase === "starting" || phase === "polling";

  const segBase =
    "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

  return (
    <div className="relative">
      <div
        role="radiogroup"
        aria-label="Browser engine"
        className="flex items-center rounded-full border border-shell-border bg-shell-surface p-[3px]"
      >
        <button
          type="button"
          role="radio"
          aria-checked={!isStreamed}
          aria-label="Proxy browser"
          title="URL-rewriting proxy browser"
          onClick={goProxy}
          className={[
            segBase,
            !isStreamed
              ? "bg-shell-surface-active text-shell-text"
              : "text-shell-text-secondary hover:text-shell-text",
          ].join(" ")}
        >
          <Globe size={11} aria-hidden="true" />
          Proxy
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={isStreamed}
          aria-label="Streamed browser"
          title="Real Chromium streamed from the host over WebRTC"
          disabled={busy}
          onClick={goStreamed}
          className={[
            segBase,
            isStreamed
              ? "bg-shell-surface-active text-shell-text"
              : "text-shell-text-secondary hover:text-shell-text",
            busy ? "opacity-60" : "",
          ].join(" ")}
        >
          <MonitorPlay size={11} aria-hidden="true" />
          {busy ? "Starting…" : "Streamed"}
        </button>
      </div>

      {phase === "no_node" && (
        <div
          role="alert"
          className="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-lg border border-shell-border-strong bg-shell-bg-glass px-3 py-2 text-[11px] leading-relaxed text-shell-text-secondary shadow-window backdrop-blur-md"
        >
          A streamed browser needs a more capable device on your taOS. Add one to
          enable this.
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setPhase("idle")}
            className="ml-2 font-semibold text-shell-text hover:text-accent"
          >
            OK
          </button>
        </div>
      )}
    </div>
  );
}
