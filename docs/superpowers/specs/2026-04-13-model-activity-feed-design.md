# Model Activity Feed — Design Spec

## Overview

Add a real-time event feed to the Activity app showing model load/unload events with caller attribution. Operators can see exactly what loaded each model, when, and why — critical for debugging memory usage on resource-constrained devices like RK3588.

## Problem

Currently there's no visibility into model lifecycle events. When memory is unexpectedly high, the only way to investigate is `ps aux` and `/api/ps`. There's no history — once a model unloads, the evidence is gone.

## Architecture

### Event Flow

```
rkllama (load/unload) → webhook → taOS backend → event store → Activity app
```

### 1. rkllama Webhook Emitter

Add optional webhook support to rkllama. On model load or unload, POST an event to a configurable URL.

**Config** (`default.ini`):
```ini
[webhooks]
event_url =
```

**Event payload**:
```json
{
  "event": "model.loaded",
  "model": "qwen3-embedding-0.6b",
  "loaded_by": "python-httpx/0.27.0",
  "timestamp": "2026-04-13T22:30:33Z",
  "memory_mb": 935,
  "ttl_minutes": 5
}
```

```json
{
  "event": "model.unloaded",
  "model": "qwen3-embedding-0.6b",
  "reason": "ttl_expired",
  "loaded_duration_seconds": 300,
  "timestamp": "2026-04-13T22:35:33Z"
}
```

Unload reasons: `ttl_expired`, `manual`, `memory_pressure`, `force_killed`

### 2. taOS Event Receiver

New endpoint: `POST /api/events/model`

Receives webhook events from rkllama, stores them in the existing archive/event system. Also polls `/api/ps` periodically to capture state for models that loaded before the webhook was configured.

### 3. taOS Event Store

Append-only JSONL file at `data/events/model-activity.jsonl`. One line per event. Rotate daily or at 10MB. Keep 30 days.

### 4. Activity App — Feed View

New tab in the Activity app: "Model Activity"

**Feed items show:**
- Model name + icon (brain for LLM, vector for embedding)
- Event type badge: "Loaded" (green) / "Unloaded" (grey) / "Force Killed" (red)
- `loaded_by` — who triggered it
- Timestamp (relative: "5 minutes ago")
- Memory usage for load events
- Duration for unload events ("was loaded for 4m 32s")

**Filters:**
- By model name
- By event type (load/unload)
- By caller (loaded_by)
- Time range

### 5. Current State Panel

Above the feed, show current state from `/api/ps`:
- Currently loaded models with loaded_by, memory, time since load, TTL countdown
- Total NPU memory in use

## API

### `GET /api/events/model?limit=50&offset=0&model=&type=&loaded_by=`

Returns paginated event history.

### `GET /api/events/model/current`

Returns current `/api/ps` data proxied from rkllama, enriched with memory percentages.

## Implementation Notes

- rkllama webhook is fire-and-forget (non-blocking, ignore failures)
- taOS receiver validates payload and appends to JSONL
- Activity app polls `/api/events/model/current` every 10s for live state
- Feed loads on scroll (pagination)
- No database needed — JSONL is sufficient for this volume

## Out of Scope

- Alerting on high memory (future)
- Model load approval/blocking (future)
- Cross-cluster model activity aggregation (future)
