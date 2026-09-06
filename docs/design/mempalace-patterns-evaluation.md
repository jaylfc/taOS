# MemPalace Memory Organisation Patterns — Evaluation for taOS

Issue: [#195](https://github.com/jaylfc/taOS/issues/195)
Date: 2026-07-18

## Summary

Evaluated MemPalace (github.com/MemPalace/mempalace) organisational patterns for
potential integration into taOS's existing memory system (UserMemoryStore +
KnowledgeStore + QMD). This report covers what's worth adopting, what's not,
and maps each concept to our existing stack.

## Source materials

- MemPalace README + source (57k stars, 7k forks, 1,529 commits)
- lhl/agentic-memory ANALYSIS-mempalace.md (independent review, ~Apr 2026)
- Arxiv paper: "Spatial Metaphors for LLM Memory: A Critical Analysis of the
  MemPalace Architecture" (arxiv 2604.21284)
- taOS codebase: `tinyagentos/user_memory.py`, `tinyagentos/knowledge_store.py`,
  `tinyagentos/qmd_db.py`

## What MemPalace does well

1. **96.6% recall@5** on LongMemEval (but see caveats below — this is ChromaDB,
   not MemPalace architecture)
2. **Hierarchical Palace metaphor**: Wings → Rooms → Halls → Closets → Drawers
3. **Temporal knowledge graph**: entity triples with validity windows
4. **Multi-layer context loading**: L0–L3 with strict token budgets
5. **Memory type classification**: facts/events/discoveries/preferences/advice
6. **Zero-LLM write path**: deterministic extraction, offline, zero API cost
7. **Very low wake-up cost**: ~170 tokens for L0+L1

## Caveats (from independent analysis)

- **Headline 96.6% benchmark is ChromaDB, not MemPalace.** The palace structure
  (wings/rooms/halls) is not involved in the "raw mode" that achieves this score.
- **AAAK "30x compression, zero information loss" is false.** LongMemEval drops
  from 96.6% to 84.2% in AAAK mode — a measurable 12.4pp quality loss.
- **Contradiction detection is not implemented** despite README claims.
- **ChromaDB dependency** — MemPalace uses ChromaDB as its sole vector store.
  taOS uses SQLite FTS5 + QMD (content vectors); no ChromaDB in our stack.
- **No decay/forgetting, no hybrid search, no feedback loops.**

## Evaluation against taOS memory stack

### Existing stores

| Store | Purpose | Key fields |
|-------|---------|------------|
| `UserMemoryStore` | User memory chunks | `hash`, `user_id`, `collection`, `title`, `content`, `metadata` (JSON), `created_at` |
| `KnowledgeStore` | Knowledge items + FTS + snapshots | `id`, `source_type`, `source_url`, `title`, `summary`, `content`, `categories` (JSON), `tags` (JSON), `metadata` (JSON), `status`, `created_at`, `updated_at`, `user_id` |
| `QMD` (qmd_db.py) | Content vectors + FTS | `store_collections`, `documents`, `content`, `content_vectors`, `documents_fts` |

### Pattern 1: Temporal knowledge graph

**MemPalace approach**: SQLite entity/triple tables with `valid_from`/`valid_to` ISO date columns.
Queries filter by `as_of` timestamp. Flat triple lookup (no multi-hop graph traversal).

**taOS gap**: `user_memory_chunks` has only `created_at`. `knowledge_items` has
`created_at` + `updated_at`. `knowledge_snapshots` tracks content hashes over
time but doesn't provide point-in-time knowledge queries.

**Recommendation: ADOPT (modified)**
Add `valid_from` and `valid_to` columns (optional ISO 8601) to `user_memory_chunks`
and a new `memory_triples` table:

```sql
CREATE TABLE IF NOT EXISTS memory_triples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL DEFAULT 1.0,
    source_hash TEXT REFERENCES user_memory_chunks(hash),
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt_subject ON memory_triples(subject);
CREATE INDEX IF NOT EXISTS idx_mt_predicate ON memory_triples(predicate);
CREATE INDEX IF NOT EXISTS idx_mt_valid ON memory_triples(valid_from, valid_to);
```

This stays in SQLite (no ChromaDB dependency) and layers on top of existing
user_memory_chunks. Start simple: flat triple lookup with temporal filtering,
same as MemPalace. Multi-hop graph traversal is a future enhancement.

**Effort**: ~2 hours (new table + store class + basic API).

### Pattern 2: Memory type classification

**MemPalace approach**: Halls (hall_facts, hall_events, hall_discoveries,
hall_preferences, hall_advice) as metadata strings on every drawer. Used for
filtering but not structurally enforced.

**taOS gap**: `user_memory_chunks` has a `collection` field (default "snippets")
but no memory-type tagging. `knowledge_items` has `categories` (JSON array) and
`tags` (JSON array), closer to what we need.

**Recommendation: ADOPT (lightweight)**
Add `memory_type` to the existing `metadata` JSON blob on `user_memory_chunks`
rather than adding a new column. Types: `fact`, `event`, `preference`, `decision`,
`discovery`. No schema migration needed — just convention.

For `knowledge_items`, `categories` already serves this purpose but could benefit
from standardised values.

**Effort**: ~30 min (convention doc + search helper that filters by memory_type).

### Pattern 3: Hierarchical organisation

**MemPalace approach**: Wings → Rooms → Halls → Tunnels. Wings = people/projects,
Rooms = topics, Halls = memory types, Tunnels = cross-wing connections (same room
name in 2+ wings).

**taOS mapping**:
- Wings = `projects/` directories (already exists!)
- Rooms = topics within a project (could use `collection` field)
- Halls = memory types (see Pattern 2)
- Tunnels = cross-project memory links (new concept)

**Recommendation: ADOPT (partial, via conventions)**
Use structured `collection` names in `user_memory_chunks`:
`<project_slug>/<topic>` → e.g., `taos/cluster`, `taos/memory`, `skald/dispatcher`.

This provides hierarchical scoping for free using the existing `collection` field
without schema changes. Cross-project tunnels can be implemented as a new link
table later.

**Effort**: ~30 min (convention doc + updated search helpers).

### Pattern 4: Multi-layer context loading

**MemPalace approach**:

| Layer | Content | Tokens | When loaded |
|-------|---------|--------|-------------|
| L0 | Agent + user identity | ~50-100 | Always |
| L1 | Top-15 by importance | ~500-800 | Always |
| L2 | Wing/room-scoped recall | ~200-500 | On topic trigger |
| L3 | Full semantic search | Unbounded | Explicit query |

**taOS gap**: Current approach is flat — dump everything into context. No
progressive loading, no token budgets, no automatic scoping.

**Recommendation: ADOPT (high value)**
This is the single highest-value pattern. It would fundamentally improve how
agents consume memory. Implementation approach:

1. **L0** — Agent identity file + user preferences (already partially in system
   prompt generation in `routes/agents.py`)
2. **L1** — Top-N most important/recent memories for the current project
3. **L2** — Topic-specific recall (triggered by keyword/embedding match)
4. **L3** — Full FTS5 + QMD vector search

This can be implemented as a helper function used by agent route handlers, not a
new store. taOS's FTS5 already provides fast keyword search; QMD provides
vector search for L3.

**Effort**: ~4 hours (new `memory_context.py` module with progressive loading
helper + integration into agent system prompt builder).

### Pattern 5: Raw storage vs summarisation

**MemPalace approach**: Store everything verbatim in drawers; AAAK compression
as a separate lossy layer (10-30x compression, 12pp quality drop).

**taOS status**: Already stores verbatim content in `user_memory_chunks.content`
and `knowledge_items.content`. `KnowledgeStore` also has `summary` field — good.
QMD stores content vectors separately. No compression layer.

**Recommendation: KEEP CURRENT APPROACH**
taOS already follows best practice: store raw text + optional summaries. The
AAAK compression is lossy (12pp drop) and not worth the complexity. LLM-based
summarisation (already possible with taOS agents) is more appropriate.

### Pattern 6: Agent diary / per-agent persistent memory

**MemPalace approach**: Each agent gets its own wing with timestamped diary
entries in the same ChromaDB collection.

**taOS status**: `chat/message_store.py` stores conversation messages with
`agent_name`. `user_memory_chunks` has `user_id` scoping. Partial coverage.

**Recommendation: NOT NOW**
The per-agent diary concept is interesting but overlaps with existing chat
history + user memory. Let the multi-layer context loading (Pattern 4) handle
this via project-scoped recall.

## What NOT to adopt

1. **ChromaDB as sole vector store** — taOS uses SQLite FTS5 + QMD; no benefit
   from introducing a new dependency.
2. **AAAK lossy compression** — 12pp quality drop is too high; LLM summarisation
   is more appropriate.
3. **Palace graph computed from metadata scans** — O(n) per graph build doesn't
   scale; if we need a graph, build it properly with edges and weights.
4. **Zero-LLM write path** — taOS agents already have LLM access; rule-based
   extraction would be a downgrade.

## Implementation plan (recommended order)

1. **Multi-layer context loading** (Pattern 4) — highest impact, ~4 hours
   - New `tinyagentos/memory_context.py` module
   - Progressive L0→L3 loading helper
   - Integration into agent system prompt builder
   - Tests: `tests/test_memory_context.py`

2. **Memory type classification** (Pattern 2) — lightweight, ~30 min
   - Convention doc + type constants
   - Search helper with type filtering
   - No schema changes

3. **Temporal knowledge graph** (Pattern 1) — next priority, ~2 hours
   - New `memory_triples` table
   - `MemoryTripleStore` class
   - Point-in-time query API

4. **Hierarchical organisation** (Pattern 3) — conventions only, ~30 min
   - Structured collection naming convention
   - Updated search helpers

## References

- MemPalace: https://github.com/MemPalace/mempalace
- Independent analysis: https://github.com/lhl/agentic-memory/blob/main/ANALYSIS-mempalace.md
- Arxiv paper: https://arxiv.org/html/2604.21284v1
- taOS user_memory.py: `tinyagentos/user_memory.py`
- taOS knowledge_store.py: `tinyagentos/knowledge_store.py`
- taOS qmd_db.py: `tinyagentos/qmd_db.py`
