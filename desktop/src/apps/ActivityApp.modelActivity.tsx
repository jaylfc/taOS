import { useEffect, useMemo, useRef, useState } from "react";
import {
  Brain, Boxes, Code2, Eye, Image as ImageIcon, Mic, Radio, RefreshCw, SlidersHorizontal,
} from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import { createSseConnection } from "@/lib/sse";
import { formatRelativeSeconds } from "@/lib/cluster";
import {
  fetchModelActivity,
  formatDuration,
  formatTokens,
  mergeModelActivityEvents,
  modelActivityStreamUrl,
  modelEventLabel,
  modelEventTone,
  modelIconKind,
  MODEL_EVENT_REQUEST_FINISH,
  MAX_MODEL_ACTIVITY_EVENTS,
  type ModelActivityEvent,
  type ModelActivityFilters,
} from "@/lib/model-activity-api";

// #208: the Model Activity panel. A timeline of model-level events -- load,
// unload, eviction, shrink, route change and inference request lifecycle --
// seeded from GET /api/activity/models and then kept live over SSE from
// GET /api/activity/models/stream.
//
// NOTE: this is NOT the AI-stack manager (ActivityApp.aiStack.tsx). That
// surface lists installed/loaded stack units; this one is an event feed.

/** Ring the panel keeps in memory. Shorter than the server's 500-record ring on
 *  purpose: the panel renders a bounded list, and the history fetch plus the
 *  live frames are merged into it. */
const MAX_EVENTS = MAX_MODEL_ACTIVITY_EVENTS;
/** No SSE replay -- the history fetch already delivered that window. */
const STREAM_REPLAY = 0;
/** Cap on the seq de-dupe set, mirroring lib/sse.ts MAX_SEEN_IDS. */
const MAX_SEEN_SEQS = 512;

const ICONS = {
  brain: Brain,
  vector: Boxes,
  image: ImageIcon,
  audio: Mic,
  vision: Eye,
  code: Code2,
} as const;

function union(existing: string[], incoming: string[]): string[] {
  const out = new Set(existing);
  for (const value of incoming) {
    if (value) out.add(value);
  }
  return [...out].sort();
}

export function ModelActivityPanel() {
  const [worker, setWorker] = useState("");
  const [model, setModel] = useState("");
  const [event, setEvent] = useState("");
  const [events, setEvents] = useState<ModelActivityEvent[]>([]);
  const [workers, setWorkers] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const seen = useRef<{ order: number[]; set: Set<number> }>({ order: [], set: new Set() });

  useEffect(() => {
    const active: ModelActivityFilters = {};
    if (worker) active.worker = worker;
    if (model) active.model = model;
    if (event) active.event = event;

    let cancelled = false;
    seen.current = { order: [], set: new Set() };
    setEvents([]);
    setLoading(true);
    setError(null);

    // Bounded seq de-dupe: the set is capped so a long-lived feed cannot grow
    // it without limit (same shape as lib/sse.ts MAX_SEEN_IDS).
    const markSeen = (seq: number): boolean => {
      const s = seen.current;
      if (s.set.has(seq)) return false;
      s.set.add(seq);
      s.order.push(seq);
      if (s.order.length > MAX_SEEN_SEQS) {
        const oldest = s.order.shift();
        if (oldest !== undefined) s.set.delete(oldest);
      }
      return true;
    };

    const absorb = (incoming: ModelActivityEvent[]) => {
      if (incoming.length === 0) return;
      setWorkers((prev) => union(prev, incoming.map((e) => e.worker)));
      setModels((prev) => union(prev, incoming.map((e) => e.model)));
    };

    fetchModelActivity({ ...active, limit: MAX_EVENTS })
      .then((res) => {
        if (cancelled) return;
        for (const ev of res.events) markSeen(ev.seq);
        absorb(res.events);
        // MERGE, don't replace: live frames may already have landed while this
        // request was in flight, and with no SSE replay a replace would drop
        // them for good.
        setEvents((prev) => mergeModelActivityEvents(res.events, prev));
        if (res.event_types.length > 0) setEventTypes(res.event_types);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load model activity");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // SSE needs a browser EventSource; degrade to history-only when the
    // environment has none (jsdom, older webviews) instead of throwing.
    const close = typeof EventSource === "undefined"
      ? () => {}
      : createSseConnection({
          url: modelActivityStreamUrl({ ...active, limit: STREAM_REPLAY }),
          onOpen: () => {
            if (!cancelled) setLive(true);
          },
          onError: () => {
            if (!cancelled) setLive(false);
          },
          onMessage: (msg) => {
            if (cancelled) return;
            let ev: ModelActivityEvent;
            try {
              ev = JSON.parse(msg.data) as ModelActivityEvent;
            } catch {
              return;
            }
            if (typeof ev?.seq !== "number" || !markSeen(ev.seq)) return;
            absorb([ev]);
            setEvents((prev) => mergeModelActivityEvents([ev], prev));
          },
          getMessageId: (msg) => msg.data,
        });

    return () => {
      cancelled = true;
      close();
      setLive(false);
    };
  }, [worker, model, event, reloadKey]);

  const filterOptions = useMemo(
    () => ({ workers, models, eventTypes }),
    [workers, models, eventTypes],
  );

  return (
    <Card className="col-span-12 p-4">
      <CardContent className="p-0">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Radio size={14} className={live ? "text-emerald-400" : "text-white/50"} />
            <h3 className="text-xs font-semibold text-shell-text">Model Activity</h3>
            <span
              className="text-[10px] text-shell-text-tertiary"
              data-testid="model-activity-status"
            >
              {live ? "live" : "offline"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <SlidersHorizontal size={12} className="text-shell-text-tertiary" />
            <label className="sr-only" htmlFor="model-activity-worker">Filter by worker</label>
            <select
              id="model-activity-worker"
              aria-label="Filter by worker"
              className="text-[11px] bg-white/5 border border-white/10 rounded px-1.5 py-0.5 text-shell-text-secondary"
              value={worker}
              onChange={(e) => setWorker(e.target.value)}
            >
              <option value="">All workers</option>
              {filterOptions.workers.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </select>
            <label className="sr-only" htmlFor="model-activity-model">Filter by model</label>
            <select
              id="model-activity-model"
              aria-label="Filter by model"
              className="text-[11px] bg-white/5 border border-white/10 rounded px-1.5 py-0.5 text-shell-text-secondary"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="">All models</option>
              {filterOptions.models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <label className="sr-only" htmlFor="model-activity-event">Filter by event type</label>
            <select
              id="model-activity-event"
              aria-label="Filter by event type"
              className="text-[11px] bg-white/5 border border-white/10 rounded px-1.5 py-0.5 text-shell-text-secondary"
              value={event}
              onChange={(e) => setEvent(e.target.value)}
            >
              <option value="">All events</option>
              {filterOptions.eventTypes.map((t) => (
                <option key={t} value={t}>{modelEventLabel(t)}</option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Refresh model activity"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              <RefreshCw size={12} />
            </Button>
          </div>
        </div>

        {error && <p className="text-[11px] text-red-300 mb-2">{error}</p>}

        <div className="space-y-1" data-testid="model-activity-list">
          {events.length === 0 && (
            <p className="text-[11px] text-shell-text-tertiary py-2">
              {loading ? "Loading…" : "No model activity yet."}
            </p>
          )}
          {events.map((ev) => {
            const Icon = ICONS[modelIconKind(ev.model)];
            return (
              <div
                key={ev.seq}
                className="flex items-center gap-2 text-[11px] py-0.5"
                data-testid="model-activity-row"
              >
                <Icon size={12} className="text-shell-text-tertiary shrink-0" />
                <span className="text-shell-text truncate w-40">{ev.model || "unknown"}</span>
                <span className="text-shell-text-tertiary truncate w-24">{ev.worker}</span>
                <span className={`px-1.5 py-0.5 rounded shrink-0 ${modelEventTone(ev.event)}`}>
                  {modelEventLabel(ev.event)}
                </span>
                <span className="text-shell-text-tertiary w-16 text-right tabular-nums">
                  {ev.event === MODEL_EVENT_REQUEST_FINISH ? formatDuration(ev.duration_ms) : ""}
                </span>
                <span className="text-shell-text-secondary w-28 text-right tabular-nums truncate">
                  {formatTokens(ev)}
                </span>
                <span className="text-shell-text-tertiary w-20 text-right shrink-0">
                  {formatRelativeSeconds(ev.ts)}
                </span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}