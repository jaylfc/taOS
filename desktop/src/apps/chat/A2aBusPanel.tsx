import { useState, useEffect, useRef, useCallback } from "react";
import { Radio, ChevronRight, Bot, Lock } from "lucide-react";

/*
 * Read-only viewer for the external taOSmd A2A coordination bus.
 *
 * The coordination bus is a SEPARATE service (taosmd serve) where cross-product
 * agents (@taOS, @taOSmd, @hermes) coordinate. This is DISTINCT from taOS's own
 * internal per-project a2a channels. Everything here is read-only: the controller
 * exposes a read-only proxy (/api/a2a/bus/*) and this panel only fetches; there
 * is no compose box and no write path.
 *
 * Two pieces live here:
 *   - A2aBusSection: the collapsible sidebar group listing bus channels.
 *   - A2aBusMessageView: the detail pane shown when a bus channel is selected.
 * State is isolated from the project-channel logic in MessagesApp.
 */

const POLL_MS = 8000;

export interface BusChannel {
  channel: string;
  members?: string[];
  message_count?: number;
  created_ts?: number;
  last_ts?: number;
}

export interface BusMessage {
  id: number;
  ts: number;
  from: string;
  body: string;
  thread: string;
  reply_to: number | null;
}

/*
 * Kept local: the existing relative-time helpers in chat/ (AllThreadsList,
 * SearchPanel) and lib/cluster.ts are unexported copies with incompatible
 * shapes -- they call Date.now() internally and return the verbose "Xm ago"
 * form. This view needs a passed-in nowMs (a 60s-ticking state value powers the
 * re-render) and the compact "Xm" form for the tabular timestamp column, so
 * there is no reusable helper to fold into here.
 */
function busRelativeTime(ts: number | undefined, nowMs: number): string {
  if (!ts) return "";
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const mins = Math.floor((nowMs - ms) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/**
 * Hook owning the bus channel list + availability, polled every 8s.
 */
export function useBusChannels() {
  const [channels, setChannels] = useState<BusChannel[]>([]);
  const [available, setAvailable] = useState<boolean>(true);
  const [loaded, setLoaded] = useState(false);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch("/api/a2a/bus/channels");
      const data = await res.json();
      setChannels(Array.isArray(data.channels) ? data.channels : []);
      setAvailable(data.available !== false);
    } catch {
      setChannels([]);
      setAvailable(false);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    fetchChannels();
    const id = setInterval(fetchChannels, POLL_MS);
    return () => clearInterval(id);
  }, [fetchChannels]);

  return { channels, available, loaded };
}

/* ------------------------------------------------------------------ */
/*  Sidebar group                                                      */
/* ------------------------------------------------------------------ */

export function A2aBusSection({
  channels,
  available,
  loaded,
  selected,
  onSelect,
}: {
  channels: BusChannel[];
  available: boolean;
  loaded: boolean;
  selected: string | null;
  onSelect: (channel: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  // Hide the whole group until the first fetch resolves so it never flashes.
  if (!loaded) return null;

  // The bus reads as its own protected rail: a tinted accent-soft container
  // with an accent hairline, distinct from the plain project channels.
  return (
    <div className="mx-2.5 mt-2.5 rounded-2xl bg-accent-soft border border-accent-line p-1.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="a2a-bus-channels"
        className="flex items-center gap-1.5 px-2 pt-1 pb-1.5 text-[10.5px] font-bold uppercase tracking-wide text-accent-strong hover:opacity-80 transition-opacity w-full text-left"
      >
        <ChevronRight
          size={11}
          aria-hidden="true"
          className={`transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <Radio size={12} aria-hidden="true" />
        Coordination Bus
      </button>
      {expanded && (
        <div id="a2a-bus-channels">
          {!available ? (
            <div className="px-2 py-2">
              <p className="text-[11px] text-shell-text-secondary">Coordination bus offline</p>
              <p className="text-[10px] text-shell-text-tertiary mt-0.5">No bus service reachable.</p>
            </div>
          ) : channels.length === 0 ? (
            <div className="px-2 py-1 text-[11px] text-shell-text-tertiary italic">No channels yet</div>
          ) : (
            channels.map((ch) => (
              <button
                key={ch.channel}
                type="button"
                onClick={() => onSelect(ch.channel)}
                aria-pressed={selected === ch.channel}
                aria-label={`Coordination channel ${ch.channel}`}
                title="Read-only agent coordination channel"
                className={`w-full text-left py-1.5 px-2 rounded-[10px] text-[13px] flex items-center gap-2.5 transition-colors ${
                  selected === ch.channel ? "bg-shell-surface-active" : "hover:bg-shell-surface-hover"
                }`}
              >
                <span className="shrink-0 grid place-items-center w-[30px] h-[30px] rounded-[9px] bg-accent-soft border border-accent-line text-accent-strong">
                  <Radio size={15} aria-hidden />
                </span>
                <span className="flex-1 min-w-0">
                  <span className="flex items-baseline gap-2">
                    <span className="truncate flex-1 font-semibold text-shell-text">{ch.channel}</span>
                    <span className="shrink-0 text-[10.5px] text-shell-text-tertiary tabular-nums">
                      {(ch.members?.length ?? 0)} · {busRelativeTime(ch.last_ts, nowMs)}
                    </span>
                  </span>
                </span>
                <span className="shrink-0 inline-flex items-center gap-1 text-[9.5px] font-bold uppercase tracking-wide text-accent-strong">
                  <Lock size={10} aria-hidden /> RO
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail pane                                                        */
/* ------------------------------------------------------------------ */

export function A2aBusMessageView({ channel }: { channel: string }) {
  const [messages, setMessages] = useState<BusMessage[]>([]);
  const [available, setAvailable] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const fetchMessages = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/a2a/bus/messages?channel=${encodeURIComponent(channel)}&limit=100`,
      );
      const data = await res.json();
      setMessages(Array.isArray(data.messages) ? data.messages : []);
      setAvailable(data.available !== false);
    } catch {
      setMessages([]);
      setAvailable(false);
    } finally {
      setLoaded(true);
    }
  }, [channel]);

  useEffect(() => {
    setLoaded(false);
    fetchMessages();
    const id = setInterval(fetchMessages, POLL_MS);
    return () => clearInterval(id);
  }, [fetchMessages]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  // Stick to the bottom (newest message) only when the user is already there,
  // so an 8s poll never yanks them away from history they scrolled up to read.
  const nearBottomRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && nearBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  // Reset stickiness on channel change so a fresh load pins to the bottom.
  useEffect(() => {
    nearBottomRef.current = true;
  }, [channel]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

  return (
    <div className="relative flex-1 flex flex-col min-w-0 h-full">
      {/* header */}
      <div className="px-4 py-2.5 border-b border-shell-border flex items-center gap-3 shrink-0">
        <span className="shrink-0 grid place-items-center w-8 h-8 rounded-[9px] bg-accent-soft border border-accent-line text-accent-strong">
          <Radio size={15} aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <div className="text-[15px] font-bold tracking-tight text-shell-text truncate">{channel}</div>
          <div className="text-[11.5px] text-shell-text-secondary">Coordination bus · read-only</div>
        </div>
      </div>

      {/* messages */}
      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 py-3">
        {!loaded ? (
          <div className="h-full flex items-center justify-center text-white/20 text-sm">
            Loading…
          </div>
        ) : !available ? (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2 px-6">
            <Radio size={32} className="text-shell-text-tertiary" aria-hidden="true" />
            <p className="text-sm font-medium text-shell-text-secondary">Coordination bus offline</p>
            <p className="text-[12px] text-shell-text-tertiary">
              The bus service is not reachable right now.
            </p>
          </div>
        ) : messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2 px-6">
            <Bot size={32} className="text-shell-text-tertiary" aria-hidden="true" />
            <p className="text-sm font-medium text-shell-text-secondary">No messages yet</p>
            <p className="text-[12px] text-shell-text-tertiary">
              Agent coordination will appear here.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {messages.map((m) => (
              <div key={m.id} className="flex gap-3">
                <span className="shrink-0 grid place-items-center w-9 h-9 rounded-[11px] bg-accent-soft border border-accent-line text-accent-strong">
                  <Bot size={16} aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 mb-0.5">
                    <span className="text-[13.5px] font-bold tracking-tight text-accent-strong truncate">
                      {m.from}
                    </span>
                    <span className="text-[10.5px] text-shell-text-tertiary tabular-nums shrink-0">
                      {busRelativeTime(m.ts, nowMs)}
                    </span>
                  </div>
                  <div className="text-[14px] leading-[1.5] text-shell-text whitespace-pre-wrap break-words">
                    {m.body}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* read-only footer — tinted accent banner, no composer. Bottom inset
          keeps it clear of the phone home indicator / dock edge; env() is 0 on
          desktop so layout is unchanged. */}
      <div
        className="flex items-center gap-2.5 px-4 py-2.5 border-t border-shell-border bg-accent-soft text-[12px] text-shell-text-secondary shrink-0"
        style={{ paddingBottom: "calc(0.625rem + env(safe-area-inset-bottom, 0px))" }}
      >
        <Lock size={14} aria-hidden="true" className="shrink-0 text-accent-strong" />
        <span>
          <span className="font-semibold text-accent-strong">Read-only.</span>{" "}
          The coordination bus is managed by the agents. Mention{" "}
          <code className="font-mono text-[11.5px]">@slug</code> in a project channel to hand off.
        </span>
      </div>
    </div>
  );
}
