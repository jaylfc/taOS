# Runbook: Querying Agent Traces

**Audience:** taOS admins, librarian integrators, cost-attribution analysts.
**Auth:** all examples use the local token. Adapt for browser sessions.

---

## Purpose

The per-agent trace store captures every LLM call, message boundary, tool
invocation, and lifecycle event for a running agent. Three primary uses:

1. **Librarian data.** taOSmd reads traces to build summaries and semantic
   memory. The store is the zero-loss input layer — the librarian decides
   what to summarise vs. keep verbatim, but it never deletes raw envelopes.
2. **Incident forensics.** When an agent misbehaves, traces give you the exact
   sequence of LLM calls, token counts, errors, and tool results.
3. **Cost attribution.** Every `llm_call` event carries `cost_usd`, `tokens_in`,
   and `tokens_out`. Sum them over a time range or model to see where spend is
   going.

---

## Where traces live on disk

```
{data_dir}/agent-home/{slug}/.taos/trace/
    YYYY-MM-DDTHH.db       primary (aiosqlite, one per UTC hour)
    YYYY-MM-DDTHH.jsonl    fallback (appended on DB write failure)
```

The directory is 0700. Files travel with the agent's home folder on archive,
restore, and backup. See `docs/design/framework-agnostic-runtime.md`
("Per-agent trace capture") for the design rationale.

---

## API surface

### POST /api/trace — write an event

Used by the LiteLLM callback and in-container runtimes. Not typically called
by humans directly.

```bash
curl -s -X POST http://127.0.0.1:6969/api/trace \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "tom",
    "kind": "lifecycle",
    "payload": {"event": "started", "reason": "manual test"}
  }'
```

Response: `{"id": "<uuid>", "agent_name": "tom", "schema_version": 1}`

### GET /api/agents/{name}/trace — read events

Returns events newest-first across all hourly bucket files. Merges DB rows
and JSONL fallback lines before sorting.

```bash
curl -s "http://127.0.0.1:6969/api/agents/tom/trace?limit=50" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq
```

**Query parameters:**

| Parameter    | Type    | Description |
|---|---|---|
| `kind`       | string  | Filter to one kind (e.g. `llm_call`) |
| `channel_id` | string  | Filter to a specific chat channel |
| `trace_id`   | string  | Filter to a single trace span |
| `since`      | float   | Unix timestamp (seconds) — earliest event |
| `until`      | float   | Unix timestamp (seconds) — latest event |
| `limit`      | integer | Max events returned (1–1000, default 100) |

### POST /api/lifecycle/notify — arm keep-alive timer

Used by the LiteLLM callback after every completion to reset the idle
keep-alive timer for image-gen backends.

```bash
curl -s -X POST http://127.0.0.1:6969/api/lifecycle/notify \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H "Content-Type: application/json" \
  -d '{"backend_name": "sd-cpp"}'
```

---

## Query examples

**All `llm_call` events in the last hour for agent `tom`:**

```bash
NOW=$(date +%s)
SINCE=$((NOW - 3600))
curl -s "http://127.0.0.1:6969/api/agents/tom/trace?kind=llm_call&since=${SINCE}&limit=100" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq '.events[]'
```

**Every event in a specific trace span:**

```bash
curl -s "http://127.0.0.1:6969/api/agents/tom/trace?trace_id=<trace_id>&limit=200" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq '.events[]'
```

**All errors (any kind) for agent `tom` in the last 24 hours:**

```bash
NOW=$(date +%s)
SINCE=$((NOW - 86400))
curl -s "http://127.0.0.1:6969/api/agents/tom/trace?kind=error&since=${SINCE}&limit=200" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq '.events[] | {id, created_at, .payload.message}'
```

**All events in a specific DM channel:**

```bash
curl -s "http://127.0.0.1:6969/api/agents/tom/trace?channel_id=<channel_id>&limit=500" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq '.events | length'
```

---

## Reading the envelope

Every event is a flat dict with these fields:

| Field          | Type    | Description |
|---|---|---|
| `v`            | int     | Schema version (currently 1) |
| `id`           | string  | UUID hex — primary key, idempotent |
| `trace_id`     | string? | Groups related events into a span |
| `parent_id`    | string? | Parent event id for tree structures |
| `created_at`   | float   | Unix timestamp (seconds, UTC) |
| `agent_name`   | string  | Agent slug |
| `kind`         | string  | See kinds table below |
| `channel_id`   | string? | Chat channel for this event |
| `thread_id`    | string? | Thread within a channel |
| `backend_name` | string? | Backend that handled the call |
| `model`        | string? | Model id used |
| `duration_ms`  | int?    | Wall time in milliseconds |
| `tokens_in`    | int?    | Prompt tokens |
| `tokens_out`   | int?    | Completion tokens |
| `cost_usd`     | float?  | Cost in USD (from LiteLLM `response_cost`) |
| `error`        | string? | Error message if this event represents a failure |
| `payload`      | dict    | Kind-specific fields (see below) |

### Kinds and payload shapes

| Kind           | Payload fields |
|---|---|
| `llm_call`     | `status` (success\|failure), `messages` (list), `response` (str), `metadata` (dict) |
| `message_in`   | `from` (str), `text` (str), `attachments` (list?), `content_blocks` (list?) |
| `message_out`  | `content` (str), `content_blocks` (list?) |
| `tool_call`    | `tool` (str), `args` (dict), `caller` (str) |
| `tool_result`  | `tool` (str), `result` (any), `success` (bool) |
| `reasoning`    | `text` (str), `block_type` (str?) |
| `error`        | `stage` (str), `message` (str), `traceback` (str?) |
| `lifecycle`    | `event` (str), `reason` (str?) |

---

## Direct SQLite access (admin)

For bulk queries or merging across time ranges, query the bucket files
directly. The registry must not have those files open; stop taOS or use a
read-only connection with WAL mode.

```bash
# Most recent 20 llm_call events for agent 'tom' in the current hour
HOUR=$(date -u +"%Y-%m-%dT%H")
DB="data/agent-home/tom/.taos/trace/${HOUR}.db"

sqlite3 "$DB" \
  "SELECT id, created_at, model, tokens_in, tokens_out, cost_usd
   FROM trace_events
   WHERE kind = 'llm_call'
   ORDER BY created_at DESC
   LIMIT 20;"
```

**Merging .db and .jsonl for the same bucket:**

```python
import sqlite3, json, pathlib

slug = "tom"
bucket = "2026-04-16T14"
base = pathlib.Path(f"data/agent-home/{slug}/.taos/trace")

# From DB
rows = []
db = base / f"{bucket}.db"
if db.exists():
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM trace_events ORDER BY created_at DESC"
    ).fetchall()]
    con.close()

# From JSONL fallback
jl = base / f"{bucket}.jsonl"
if jl.exists():
    for line in jl.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))

rows.sort(key=lambda e: e.get("created_at", 0), reverse=True)
```

A future `.late.jsonl` file uses the same format; include it in the same
merge loop when it appears.

---

## Cost attribution recipe

Sum `cost_usd` grouped by model across a time range:

```bash
SINCE=$(date -u -d "yesterday" +%s 2>/dev/null || date -u -v-1d +%s)

curl -s "http://127.0.0.1:6969/api/agents/tom/trace?kind=llm_call&since=${SINCE}&limit=1000" \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  | jq '[.events[] | select(.cost_usd != null)] | group_by(.model) |
       map({model: .[0].model, total_cost: (map(.cost_usd) | add),
            calls: length, tokens_in: (map(.tokens_in // 0) | add),
            tokens_out: (map(.tokens_out // 0) | add)})'
```

For multi-agent attribution, repeat per agent slug or query each agent's
trace endpoint and aggregate in the calling script.

---

## Librarian consumption pattern

The taOSmd librarian reads traces newest-first. In practice it queries:

```
GET /api/agents/{name}/trace?since=<last_summarised_at>&limit=500
```

or, for direct access, queries each hourly bucket DB from the most recent
backward until it has covered the desired window.

The librarian may add annotations (e.g. a summary chunk in QMD that links
back to a `trace_id`) but it does not delete, update, or truncate raw trace
rows. Raw envelopes are append-only by convention; `INSERT OR IGNORE` on `id`
makes writes idempotent if the same event is posted twice.

---

## Related

- `tinyagentos/trace_store.py` — `AgentTraceStore`, `TraceStoreRegistry`,
  `ENVELOPE_V1_SCHEMA`
- `tinyagentos/routes/trace.py` — HTTP surface
- `tinyagentos/litellm_callback.py` — automatic `llm_call` capture
- `docs/design/framework-agnostic-runtime.md` — "Per-agent trace capture"
  (design) and "Programmatic access (local token)"
- `docs/design/user-memory.md` — how user memory and agent traces relate
