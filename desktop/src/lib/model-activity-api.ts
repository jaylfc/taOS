// Client for the Model Activity feed (#208).
//
// Two surfaces back the Activity app's "Model Activity" panel:
//   GET /api/activity/models         history snapshot (filters + limit)
//   GET /api/activity/models/stream  SSE stream of the same event shape
//
// The event vocabulary is defined server-side in tinyagentos/model_activity.py.

export interface ModelActivityEvent {
  seq: number;
  ts: number;
  event: string;
  model: string;
  worker: string;
  backend: string;
  duration_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  token_rate: number | null;
  reason: string | null;
  detail: Record<string, unknown>;
}

export interface ModelActivityFilters {
  worker?: string;
  model?: string;
  event?: string;
  limit?: number;
}

export interface ModelActivityResponse {
  events: ModelActivityEvent[];
  count: number;
  event_types: string[];
}

export const MODEL_EVENT_LOAD = "model.load";
export const MODEL_EVENT_UNLOAD = "model.unload";
export const MODEL_EVENT_EVICT = "model.evict";
export const MODEL_EVENT_SHRINK = "model.shrink";
export const MODEL_EVENT_ROUTE = "model.route";
export const MODEL_EVENT_REQUEST_START = "request.start";
export const MODEL_EVENT_REQUEST_FINISH = "request.finish";

export const MODEL_EVENT_LABELS: Record<string, string> = {
  [MODEL_EVENT_LOAD]: "Loaded",
  [MODEL_EVENT_UNLOAD]: "Unloaded",
  [MODEL_EVENT_EVICT]: "Evicted",
  [MODEL_EVENT_SHRINK]: "Shrunk",
  [MODEL_EVENT_ROUTE]: "Routed",
  [MODEL_EVENT_REQUEST_START]: "Request",
  [MODEL_EVENT_REQUEST_FINISH]: "Completed",
};

/** Badge tone per event type, matching the spec's load/unload/kill colours. */
export const MODEL_EVENT_TONES: Record<string, string> = {
  [MODEL_EVENT_LOAD]: "bg-emerald-500/15 text-emerald-300",
  [MODEL_EVENT_UNLOAD]: "bg-white/10 text-shell-text-secondary",
  [MODEL_EVENT_EVICT]: "bg-red-500/15 text-red-300",
  [MODEL_EVENT_SHRINK]: "bg-amber-500/15 text-amber-300",
  [MODEL_EVENT_ROUTE]: "bg-sky-500/15 text-sky-300",
  [MODEL_EVENT_REQUEST_START]: "bg-indigo-500/15 text-indigo-300",
  [MODEL_EVENT_REQUEST_FINISH]: "bg-teal-500/15 text-teal-300",
};

export function modelEventLabel(event: string): string {
  return MODEL_EVENT_LABELS[event] ?? event;
}

export function modelEventTone(event: string): string {
  return MODEL_EVENT_TONES[event] ?? "bg-white/10 text-shell-text-secondary";
}

/** Query string for both endpoints. `limit: 0` is meaningful (no SSE replay),
 *  so the key is emitted whenever the caller sets it explicitly. */
export function modelActivityQuery(filters: ModelActivityFilters): string {
  const params = new URLSearchParams();
  if (filters.worker) params.set("worker", filters.worker);
  if (filters.model) params.set("model", filters.model);
  if (filters.event) params.set("event", filters.event);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function modelActivityStreamUrl(filters: ModelActivityFilters): string {
  return `/api/activity/models/stream${modelActivityQuery(filters)}`;
}

export async function fetchModelActivity(
  filters: ModelActivityFilters = {},
  signal?: AbortSignal,
): Promise<ModelActivityResponse> {
  const res = await fetch(`/api/activity/models${modelActivityQuery(filters)}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) throw new Error(`model activity request failed (${res.status})`);
  const json = (await res.json()) as Partial<ModelActivityResponse>;
  return {
    events: json.events ?? [],
    count: json.count ?? 0,
    event_types: json.event_types ?? [],
  };
}

export type ModelIconKind = "brain" | "vector" | "image" | "audio" | "vision" | "code";

/** Pick a model icon from its name: the spec asks for a brain for LLMs and a
 *  vector for embeddings, extended to the other purposes the catalog uses. */
export function modelIconKind(model: string): ModelIconKind {
  const name = (model || "").toLowerCase();
  if (["embed", "bge", "e5-", "nomic", "gte"].some((k) => name.includes(k))) return "vector";
  if (["sd", "stable-diffusion", "sdxl", "flux", "dreamshaper", "lcm", "pixart"].some((k) => name.includes(k))) {
    return "image";
  }
  if (["whisper", "stt", "tts", "kokoro", "piper", "bark"].some((k) => name.includes(k))) return "audio";
  if (["vision", "llava", "moondream", "blip"].some((k) => name.includes(k))) return "vision";
  if (["code", "deepseek", "coder", "codellama"].some((k) => name.includes(k))) return "code";
  return "brain";
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  // Round to whole seconds FIRST, then split: rounding the remainder after
  // splitting yields "59m 60s" for 59500-59999 ms.
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) {
    const seconds = ms / 1000;
    return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  }
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

export function formatTokens(ev: ModelActivityEvent): string {
  if (ev.tokens_out === null || ev.tokens_out === undefined) return "";
  const rate = ev.token_rate !== null && ev.token_rate !== undefined
    ? ` · ${ev.token_rate.toFixed(1)} tok/s`
    : "";
  return `${ev.tokens_out} tok${rate}`;
}

/** Cap the merged feed. The server's ring holds 500 records by default; the
 *  panel keeps a shorter window so a long-lived tab cannot grow without bound. */
export const MAX_MODEL_ACTIVITY_EVENTS = 200;

/** Merge two event lists by `seq` (server-assigned, monotonic), newest first.
 *
 *  Used instead of replacing the list when the history fetch resolves: live
 *  SSE frames can already have landed while that request was in flight, and a
 *  wholesale replace would silently drop them (they are never re-sent once the
 *  seq watermark has passed).
 */
export function mergeModelActivityEvents(
  a: ModelActivityEvent[],
  b: ModelActivityEvent[],
  max: number = MAX_MODEL_ACTIVITY_EVENTS,
): ModelActivityEvent[] {
  const bySeq = new Map<number, ModelActivityEvent>();
  for (const ev of a) bySeq.set(ev.seq, ev);
  for (const ev of b) bySeq.set(ev.seq, ev);
  return [...bySeq.values()].sort((x, y) => y.seq - x.seq).slice(0, max);
}