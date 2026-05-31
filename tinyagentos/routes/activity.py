"""Model Activity feed — live inference events stream.

Provides:
- Ring buffer (last 500 events) shared across all connected clients
- SSE endpoint: GET /api/activity/stream — pushes new events
- Publish endpoint: POST /api/activity/events — other components publish
- HTML page: GET /api/activity — timeline UI with Pico CSS + htmx SSE

Events are also injectable via the module-level ``publish_event()`` function
so scheduler hooks and proxy hooks can feed the buffer without HTTP overhead.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Activity buffer — shared ring buffer + SSE fan-out
# ---------------------------------------------------------------------------

HISTORY_MAX = 500
VALID_EVENT_TYPES = frozenset({
    "model_load", "model_unload", "model_eviction",
    "route_change", "request_start", "request_finish",
})


class ActivityBuffer:
    """Ring buffer of inference events with SSE fan-out to connected clients."""

    def __init__(self, maxlen: int = HISTORY_MAX):
        self._buffer: collections.deque[dict] = collections.deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    def publish(self, event: dict) -> None:
        """Push an event to the buffer and fan-out to all subscribers.

        ``event`` must have at least ``type`` and ``timestamp`` keys.
        Missing keys are filled with sensible defaults.
        """
        event.setdefault("timestamp", time.time())
        event.setdefault("model_id", "")
        event.setdefault("worker", "")
        event.setdefault("duration_ms", 0)
        event.setdefault("tokens_per_sec", 0.0)

        event_type = event.get("type", "")
        if event_type not in VALID_EVENT_TYPES:
            logger.warning("activity: dropping event with unknown type %r", event_type)
            return

        self._buffer.append(event)
        # Fan-out — fire-and-forget; slow clients are dropped naturally
        dead: set[asyncio.Queue] = set()
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.add(q)
        self._subscribers.difference_update(dead)

    async def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber. Returns a queue that receives events."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(q)

    def snapshot(self) -> list[dict]:
        """Return a copy of the current buffer, newest first."""
        return list(reversed(self._buffer))


# Module-level singleton — populated at startup via app.state
_buffer: Optional[ActivityBuffer] = None


def get_buffer(request: Request) -> ActivityBuffer:
    """Return the activity buffer from app state (lazy init for test compat)."""
    global _buffer
    if _buffer is not None:
        return _buffer
    buf = getattr(request.app.state, "activity_buffer", None)
    if buf is None:
        buf = ActivityBuffer()
        request.app.state.activity_buffer = buf
        _buffer = buf
    return buf


def publish_event(event: dict) -> None:
    """Publish an activity event from anywhere in the codebase.

    Safe to call before the buffer is initialised (events are dropped silently).
    """
    if _buffer is not None:
        _buffer.publish(event)


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------


def _matches_filter(event: dict, worker: str, model: str, event_type: str) -> bool:
    if worker and event.get("worker", "") != worker:
        return False
    if model and event.get("model_id", "") != model:
        return False
    if event_type and event.get("type", "") != event_type:
        return False
    return True


@router.get("/api/activity/stream")
async def activity_stream(
    request: Request,
    worker: str = Query(""),
    model: str = Query(""),
    type: str = Query(""),
):
    """SSE stream of inference activity events.

    Query params act as filters: ?worker=X&model=Y&type=Z.
    Leaving a filter empty means no filtering on that dimension.
    """
    buf = get_buffer(request)

    async def event_generator():
        q = await buf.subscribe()
        try:
            # Replay existing history first so a freshly-opened tab
            # shows recent events immediately.
            for event in buf.snapshot():
                if _matches_filter(event, worker, model, type):
                    yield f"data: {json.dumps(event)}\n\n"

            # Stream new events
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keep-alive comment — prevents proxies from closing the connection
                    yield ": keepalive\n\n"
                    continue

                if _matches_filter(event, worker, model, type):
                    yield f"data: {json.dumps(event)}\n\n"
        finally:
            await buf.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Publish endpoint — for external event sources
# ---------------------------------------------------------------------------


@router.post("/api/activity/events")
async def publish_activity_event(request: Request):
    """Accept an activity event from another component.

    Request body must be JSON with at least a ``type`` key.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    event_type = body.get("type", "")
    if not event_type:
        return JSONResponse({"error": "missing 'type' field"}, status_code=400)
    if event_type not in VALID_EVENT_TYPES:
        return JSONResponse(
            {"error": f"unknown event type {event_type!r}"}, status_code=400,
        )

    buf = get_buffer(request)
    buf.publish(body)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------


@router.get("/api/activity/history")
async def activity_history(
    request: Request,
    limit: int = Query(100, ge=1, le=HISTORY_MAX),
    worker: str = Query(""),
    model: str = Query(""),
    type: str = Query(""),
):
    """Return recent activity events as JSON (polling fallback)."""
    buf = get_buffer(request)
    events = buf.snapshot()
    filtered = [
        e for e in events
        if _matches_filter(e, worker, model, type)
    ][:limit]
    return JSONResponse({"events": filtered})


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

_ACTIVITY_FEED_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model Activity — TinyAgentOS</title>
<link rel="stylesheet" href="/static/pico.min.css">
<style>
:root {
  --timeline-dot-size: 10px;
  --timeline-line-color: var(--pico-muted-border-color, #374956);
}
body { padding: 1.5rem; }
.activity-header {
  display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end;
  margin-bottom: 1.5rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--pico-muted-border-color);
}
.activity-header h1 { margin: 0; font-size: 1.5rem; flex: 1 1 auto; }
.filter-bar { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.filter-bar select, .filter-bar button {
  margin-bottom: 0; font-size: 0.875rem;
}
.status-bar {
  display: flex; gap: 1rem; align-items: center; font-size: 0.8rem;
  color: var(--pico-muted-color); margin-bottom: 0.5rem;
}
.status-dot {
  width: 8px; height: 8px; border-radius: 50%; display: inline-block;
}
.status-dot.live { background: var(--pico-color-green-400, #4caf50); }
.status-dot.stale { background: var(--pico-color-red-400, #f44336); }
.timeline { position: relative; padding-left: 2rem; }
.timeline::before {
  content: ''; position: absolute; left: 14px; top: 0; bottom: 0;
  width: 2px; background: var(--timeline-line-color);
}
.timeline-item {
  position: relative; padding: 0.5rem 0 0.5rem 1.5rem;
  border-bottom: 1px solid var(--pico-muted-border-color, #2a3140);
}
.timeline-item:last-child { border-bottom: none; }
.timeline-dot {
  position: absolute; left: -1.55rem; top: 0.85rem;
  width: var(--timeline-dot-size); height: var(--timeline-dot-size);
  border-radius: 50%; border: 2px solid var(--pico-muted-border-color);
  background: var(--pico-background-color);
}
.timeline-dot.load  { border-color: var(--pico-color-blue-400, #42a5f5);  background: var(--pico-color-blue-400, #42a5f5); }
.timeline-dot.unload{ border-color: var(--pico-color-orange-400, #ff9800); background: var(--pico-color-orange-400, #ff9800); }
.timeline-dot.evict { border-color: var(--pico-color-red-400, #ef5350);    background: var(--pico-color-red-400, #ef5350); }
.timeline-dot.route { border-color: var(--pico-color-purple-400, #ab47bc); background: var(--pico-color-purple-400, #ab47bc); }
.timeline-dot.start { border-color: var(--pico-color-green-400, #66bb6a);  background: var(--pico-color-green-400, #66bb6a); }
.timeline-dot.finish{ border-color: var(--pico-color-teal-400, #26a69a);   background: var(--pico-color-teal-400, #26a69a); }
.event-label {
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.05em; display: inline-block; padding: 0.1rem 0.4rem;
  border-radius: 3px; margin-right: 0.5rem;
}
.event-label.load   { color: var(--pico-color-blue-300, #90caf9);   background: rgb(66 165 245 / 0.15); }
.event-label.unload { color: var(--pico-color-orange-300, #ffcc80);  background: rgb(255 152 0 / 0.15); }
.event-label.evict  { color: var(--pico-color-red-300, #ef9a9a);    background: rgb(239 83 80 / 0.15); }
.event-label.route  { color: var(--pico-color-purple-300, #ce93d8); background: rgb(171 71 188 / 0.15); }
.event-label.start  { color: var(--pico-color-green-300, #a5d6a7);  background: rgb(102 187 106 / 0.15); }
.event-label.finish { color: var(--pico-color-teal-300, #80cbc4);   background: rgb(38 166 154 / 0.15); }
.event-meta { font-size: 0.8rem; color: var(--pico-muted-color); margin-top: 0.15rem; }
.event-meta span { margin-right: 1rem; }
.event-model { font-weight: 600; color: var(--pico-color); }
#timeline-container { min-height: 200px; }
.empty-state {
  text-align: center; padding: 3rem 1rem; color: var(--pico-muted-color);
}
.empty-state svg { width: 48px; height: 48px; margin-bottom: 1rem; opacity: 0.4; }
</style>
</head>
<body>

<div class="activity-header">
  <h1>&#9889; Model Activity</h1>
  <div class="filter-bar">
    <select id="filter-type" aria-label="Filter by event type">
      <option value="">All types</option>
      <option value="model_load">Model Load</option>
      <option value="model_unload">Model Unload</option>
      <option value="model_eviction">Model Eviction</option>
      <option value="route_change">Route Change</option>
      <option value="request_start">Request Start</option>
      <option value="request_finish">Request Finish</option>
    </select>
    <select id="filter-worker" aria-label="Filter by worker">
      <option value="">All workers</option>
    </select>
    <select id="filter-model" aria-label="Filter by model">
      <option value="">All models</option>
    </select>
    <button id="btn-clear" class="outline secondary" aria-label="Clear all filters">Clear</button>
  </div>
</div>

<div class="status-bar">
  <span class="status-dot live" id="status-dot" aria-hidden="true"></span>
  <span id="status-text">Connected</span>
  <span id="event-count" style="margin-left:auto">0 events</span>
</div>

<div id="timeline-container" role="log" aria-live="polite" aria-label="Activity timeline">
  <div class="empty-state">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
    <p>Waiting for events&hellip;</p>
  </div>
</div>

<script>
// ---- State ----
let events = [];
let eventCount = 0;
const MAX_VISIBLE = 200;
let filterType = "";
let filterWorker = "";
let filterModel = "";
let workerSet = new Set();
let modelSet = new Set();
let lastEventTime = 0;
let staleTimer = null;

// ---- DOM refs ----
const container = document.getElementById("timeline-container");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const eventCountEl = document.getElementById("event-count");
const filterTypeEl = document.getElementById("filter-type");
const filterWorkerEl = document.getElementById("filter-worker");
const filterModelEl = document.getElementById("filter-model");

// ---- SVG icons ----
const ICONS = {
  model_load:    '<circle cx="12" cy="12" r="10"/><polyline points="8 12 12 8 16 12"/><line x1="12" y1="8" x2="12" y2="16"/>',
  model_unload:  '<circle cx="12" cy="12" r="10"/><polyline points="8 12 12 16 16 12"/><line x1="12" y1="16" x2="12" y2="8"/>',
  model_eviction:'<circle cx="12" cy="12" r="10"/><line x1="8" y1="8" x2="16" y2="16"/><line x1="16" y1="8" x2="8" y2="16"/>',
  route_change:  '<circle cx="12" cy="12" r="10"/><path d="M8 12a4 4 0 0 1 4-4v0a4 4 0 0 1 4 4h-8z"/><line x1="12" y1="8" x2="12" y2="6"/><line x1="12" y1="16" x2="12" y2="18"/><line x1="8" y1="12" x2="6" y2="12"/><line x1="16" y1="12" x2="18" y2="12"/>',
  request_start: '<circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/>',
  request_finish:'<circle cx="12" cy="12" r="10"/><polyline points="9 12 11 14 15 10"/>',
};

function iconSvg(type) {
  const path = ICONS[type] || ICONS.model_load;
  return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:0.3rem;opacity:0.8">' + path + '</svg>';
}

function dotClass(type) {
  const map = {model_load:'load',model_unload:'unload',model_eviction:'evict',route_change:'route',request_start:'start',request_finish:'finish'};
  return map[type] || 'load';
}

function labelClass(type) {
  return dotClass(type);
}

function labelText(type) {
  const map = {model_load:'Load',model_unload:'Unload',model_eviction:'Evict',route_change:'Route',request_start:'Start',request_finish:'Finish'};
  return map[type] || type;
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function formatDuration(ms) {
  if (!ms || ms <= 0) return "";
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

// ---- Filtering ----
function matchesFilter(ev) {
  if (filterType && ev.type !== filterType) return false;
  if (filterWorker && ev.worker !== filterWorker) return false;
  if (filterModel && ev.model_id !== filterModel) return false;
  return true;
}

function updateDropdowns(ev) {
  if (ev.worker && !workerSet.has(ev.worker)) {
    workerSet.add(ev.worker);
    const opt = document.createElement("option");
    opt.value = ev.worker;
    opt.textContent = ev.worker;
    filterWorkerEl.appendChild(opt);
  }
  if (ev.model_id && !modelSet.has(ev.model_id)) {
    modelSet.add(ev.model_id);
    const opt = document.createElement("option");
    opt.value = ev.model_id;
    opt.textContent = ev.model_id;
    filterModelEl.appendChild(opt);
  }
}

function applyFilters() {
  filterType = filterTypeEl.value;
  filterWorker = filterWorkerEl.value;
  filterModel = filterModelEl.value;
  renderAll();
}

filterTypeEl.addEventListener("change", applyFilters);
filterWorkerEl.addEventListener("change", applyFilters);
filterModelEl.addEventListener("change", applyFilters);
document.getElementById("btn-clear").addEventListener("click", () => {
  filterTypeEl.value = "";
  filterWorkerEl.value = "";
  filterModelEl.value = "";
  applyFilters();
});

// ---- Rendering ----
function renderEvent(ev) {
  const icon = iconSvg(ev.type);
  const dot = dotClass(ev.type);
  const lblCls = labelClass(ev.type);
  const lblTxt = labelText(ev.type);
  let meta = [];
  if (ev.worker) meta.push('<span aria-label="Worker">&#128421; ' + escHtml(ev.worker) + '</span>');
  if (ev.model_id) meta.push('<span class="event-model" aria-label="Model">' + escHtml(ev.model_id) + '</span>');
  if (ev.duration_ms) meta.push('<span aria-label="Duration">&#9202; ' + formatDuration(ev.duration_ms) + '</span>');
  if (ev.tokens_per_sec > 0) meta.push('<span aria-label="Speed">' + ev.tokens_per_sec.toFixed(0) + ' tok/s</span>');
  meta.push('<span aria-label="Time">' + formatTime(ev.timestamp) + '</span>');
  return (
    '<div class="timeline-item" role="listitem">' +
    '<div class="timeline-dot ' + dot + '" aria-hidden="true"></div>' +
    '<span class="event-label ' + lblCls + '">' + icon + ' ' + lblTxt + '</span>' +
    '<div class="event-meta">' + meta.join('') + '</div>' +
    '</div>'
  );
}

function escHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderAll() {
  const filtered = events.filter(matchesFilter);
  const visible = filtered.slice(0, MAX_VISIBLE);
  if (visible.length === 0) {
    container.innerHTML = '<div class="empty-state"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><p>No matching events</p></div>';
  } else {
    container.innerHTML = '<div class="timeline" role="list">' + visible.map(renderEvent).join('') + '</div>';
  }
  eventCountEl.textContent = eventCount + ' event' + (eventCount !== 1 ? 's' : '');
}

function addEvent(ev) {
  events.unshift(ev);
  eventCount++;
  updateDropdowns(ev);
  if (matchesFilter(ev)) {
    renderAll();
  }
  lastEventTime = Date.now();
  setConnected(true);
}

// ---- Connection health ----
function setConnected(live) {
  if (live) {
    statusDot.className = "status-dot live";
    statusText.textContent = "Connected";
    if (staleTimer) { clearTimeout(staleTimer); staleTimer = null; }
    staleTimer = setTimeout(() => setConnected(false), 30_000);
  } else {
    statusDot.className = "status-dot stale";
    statusText.textContent = "Disconnected — retrying\u2026";
  }
}

// ---- SSE connection ----
function buildStreamUrl() {
  const params = new URLSearchParams();
  if (filterType) params.set("type", filterType);
  if (filterWorker) params.set("worker", filterWorker);
  if (filterModel) params.set("model", filterModel);
  const qs = params.toString();
  return "/api/activity/stream" + (qs ? "?" + qs : "");
}

function connectSSE() {
  const url = buildStreamUrl();
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      addEvent(ev);
    } catch (err) {
      console.warn("activity: failed to parse event", err);
    }
  };
  es.onerror = () => {
    setConnected(false);
    es.close();
    // Reconnect after back-off
    setTimeout(connectSSE, 3_000);
  };
}

// ---- Init ----
connectSSE();
</script>
</body>
</html>"""


@router.get("/api/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    """Serve the Activity Feed UI page."""
    # Ensure buffer is initialised
    get_buffer(request)
    return HTMLResponse(_ACTIVITY_FEED_HTML)
