# Library App

## Overview

A unified frontend for browsing, searching, organising, and monitoring all content in the Knowledge Base. The Library App is the primary content management interface — the place where you curate what you've saved, manage categories and rules, control what agents can access, and track changes over time. It sits on top of the fully-built Knowledge Base Service (KnowledgeStore, IngestPipeline, MonitorService, CategoryEngine, 12 API endpoints).

Build order: #2 (immediately after the Knowledge Base Service, which is complete and merged).

---

## Architecture

Single-file app following existing codebase conventions (`LibraryApp.tsx` + `lib/knowledge.ts` for typed API helpers). Internal view state manages navigation between list and detail views. Components are extracted into separate files only when they exceed ~150 lines (likely candidates: diff viewer, rule editor).

```
desktop/src/
├── apps/LibraryApp.tsx          # Main app component, view state machine
├── lib/knowledge.ts             # Typed fetch wrappers for /api/knowledge/* endpoints
└── (extracted only if needed)
    ├── apps/library/DiffViewer.tsx
    └── apps/library/RuleEditor.tsx
```

Registered in `app-registry.ts` as:
- `id: "library"`
- `name: "Library"`
- `icon: "book-open"`
- `category: "platform"`
- `launchpadOrder: 13.5`
- `singleton: true`
- `pinned: true`
- `defaultSize: { w: 1000, h: 650 }`
- `minSize: { w: 550, h: 400 }`

---

## Layout

Two-state view controlled by internal state: `list` (default) and `detail` (after clicking an item).

### List View

```
┌─────────────────────────────────────────────────────┐
│ ┌──────────┐ ┌────────────────────────────────────┐ │
│ │ Sidebar  │ │ [Search bar]        [Keyword|Sem]  │ │
│ │          │ │                                    │ │
│ │ Sources  │ │ ┌──────────────────────────────┐   │ │
│ │  All     │ │ │ Item card                    │   │ │
│ │  Reddit  │ │ │ Title · author · source · date│  │ │
│ │  YouTube │ │ │ Summary preview...           │   │ │
│ │  GitHub  │ │ │ [AI/ML] [2 changes]          │   │ │
│ │  X       │ │ │ Shared with: research-agent  │   │ │
│ │  Articles│ │ └──────────────────────────────┘   │ │
│ │          │ │                                    │ │
│ │ Categori │ │ ┌──────────────────────────────┐   │ │
│ │  AI/ML   │ │ │ Item card                    │   │ │
│ │  Rockchip│ │ └──────────────────────────────┘   │ │
│ │  Dev     │ │                                    │ │
│ │  +Manage │ │ ┌──────────────────────────────┐   │ │
│ │          │ │ │ Item card                    │   │ │
│ │ Status   │ │ └──────────────────────────────┘   │ │
│ │  Ready   │ │                                    │ │
│ │  Process │ │                                    │ │
│ │  Error   │ │                                    │ │
│ │          │ │                                    │ │
│ │ Monitori │ │                                    │ │
│ │  Recent  │ │                                    │ │
│ │  Active  │ │                                    │ │
│ │  Slow    │ │                                    │ │
│ └──────────┘ └────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### Detail View

```
┌─────────────────────────────────────────────────────┐
│ ← Back to library                                   │
│                                                     │
│ Title (large)                                       │
│ Author · Source · Saved date                        │
│ [AI/ML] [ready] [monitoring: active]                │
│ Shared with: research-agent  + add agent            │
│                                                     │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Summary box                                     │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ [Content] [History (3)] [Metadata]                  │
│ ─────────────────────────────────────               │
│ Full text / transcript / markdown content           │
│ (scrollable)                                        │
│                                                     │
│                                                     │
│ ─────────────────────────────────────               │
│ [Open source] [Re-ingest] [Download]     [Delete]   │
└─────────────────────────────────────────────────────┘
```

Mobile: sidebar collapses to a back-navigable list (same pattern as MemoryApp).

---

## Sidebar Filters

Four collapsible sections. Each filter is a single-select toggle — clicking a selected filter deselects it. Multiple sections can be active simultaneously (e.g. source=Reddit AND category=AI/ML).

### Sources

Static list matching `source_type` values: All (default), Reddit, YouTube, GitHub, X, Articles, Files, Manual. Each shows an item count badge.

### Categories

Dynamically populated from the knowledge store. Each shows an item count. Clicking filters the list. "+ Manage" link at the bottom opens the category management dialog.

### Status

Ready, Processing, Error. Useful for seeing items stuck in pipeline or failed ingests.

### Monitoring

- **Recent changes** — items with diffs in their latest snapshot since the user last viewed them. Red dot badge with count.
- **Active polls** — items currently being polled at intervals shorter than monthly.
- **Slow items** — items that have decayed to the 30-day floor. Option to re-activate or manually disable.

---

## Item Cards

Each card displays:

| Element | Source |
|---------|--------|
| Title | `title` field |
| Author · Source type · Relative date | `author`, `source_type`, `created_at` |
| Summary (1-2 lines, truncated) | `summary` field |
| Category pills (coloured) | `categories` JSON array |
| Change badge (amber, e.g. "2 changes") | Computed from snapshots with unseen diffs |
| "Shared with: agent1, agent2" | Computed from `agent_knowledge_subscriptions` matching item categories |

**Sorting options:** Newest first (default), Recently updated, Most changes, Alphabetical.

**Pagination:** Infinite scroll using `GET /api/knowledge/items?limit=50&offset=N`. Initial load of 50 items.

---

## Detail View

Full replacement of the list view. Back link returns to the list, preserving scroll position and active filters.

### Header

- Back link ("← Back to library")
- Title (large text)
- Author · Source type · "Saved N days ago"
- Category pills + status pill (`ready`/`processing`/`error`) + monitoring status pill
- "Shared with: agent1, agent2 + add agent" — contextual inline sharing. Clicking "+ add agent" shows a dropdown of available agents. Removing an agent removes the subscription for that item's categories.

### Summary Box

LLM-generated summary in a subtle bordered card.

### Tabbed Content

**Content tab:**
- Full text, transcript, or markdown rendered content
- Scrollable independently
- For YouTube: transcript with timestamps
- For Reddit: post body + comment tree
- For articles: extracted readable text

**History tab:**
- Timeline of monitoring snapshots, newest first
- Each snapshot entry shows:
  - Timestamp
  - Content diff — inline diff with green highlights for additions, red strikethrough for deletions
  - Metadata deltas — pills showing changes (e.g. "↑ 142 → 387 upvotes", "+12 comments", "status: open → closed")
- Compare mode: pick any two snapshots to diff, not just sequential
- Deleted content preserved with original text, marked as "[deleted by author]"
- Data from `GET /api/knowledge/items/{item_id}/snapshots`

**Metadata tab:**
- Raw platform-specific data in a key-value table
- Fields from the `metadata` JSON column: subreddit, upvotes, views, like count, comment count, etc.
- Monitor config: current poll frequency, decay multiplier, next poll time, pinned status

### Action Bar

| Button | Action |
|--------|--------|
| Open source | Opens `source_url` in a new tab/browser window |
| Re-ingest | `POST /api/knowledge/ingest` with the item's URL to refresh content |
| Download media | Triggers media download (video for YouTube, full page for articles). Shows file size if already downloaded. Changes to "View local copy" when media exists at `media_path`. |
| Stop monitoring | Disables polling for this item (manual opt-out). Only way polling actually stops. |
| Delete | Removes item via `DELETE /api/knowledge/items/{item_id}` with confirmation dialog |

---

## Search

Search bar at the top of the main area with a mode toggle:

- **Keyword** — `GET /api/knowledge/search?q=...&mode=keyword` using FTS5 index on title + content + summary + author
- **Semantic** — `GET /api/knowledge/search?q=...&mode=semantic` using QMD vector search on the `knowledge` collection

Results replace the item list. Active sidebar filters still apply (search within a source type or category).

---

## Category Management (Progressive)

### Simple Layer (default)

- Category pills on items are clickable (filters the list)
- Sidebar shows all categories with item counts
- "+ Manage" link opens a management dialog

### Management Dialog

- List of all categories with item counts
- **Rename** — inline edit
- **Merge** — select two categories, combine into one (items reassigned, old category removed)
- **Delete** — removes category, items become uncategorised
- **Create** — new category name

### Rules Editor (expandable)

Hidden by default behind an "Advanced: Rules" expandable section inside the management dialog.

- Table of rules: pattern, match field (`source_url`, `source_type`, `author`, `title`, `subreddit`, `channel`), target category, priority
- Add new rule: form fields for pattern (glob), match field (dropdown), category (dropdown + create new), priority (number)
- Edit/delete existing rules
- **Test button** — given a rule, shows which existing items in the knowledge base would match. Lets you verify before saving.
- Data from `GET/POST/DELETE /api/knowledge/rules`

---

## Agent Subscriptions (Contextual)

No dedicated subscriptions section. Agent sharing is surfaced contextually wherever you're looking at items or categories.

### On Item Cards

Subtle "Shared with: agent1, agent2" line at the bottom of cards where the item's categories have agent subscriptions.

### On Item Detail

"Shared with: research-agent, dev-agent + add agent" line in the header. Clicking "+ add agent" shows a dropdown populated from `GET /api/agents`. Adding an agent creates a subscription for that item's categories via `POST /api/knowledge/subscriptions`. Removing an agent deletes the subscription via `DELETE /api/knowledge/subscriptions/{agent_name}/{category}`.

### On Category Management

Each category in the management dialog shows which agents are subscribed. Toggle auto-ingest vs notify-only per subscription.

---

## Monitoring Behaviour

### Decay with Floor (no auto-stop)

The existing MonitorService decay logic is adjusted:

- Decay multiplier still increases the polling interval after no-change polls
- **Floor: 30 days** — decay never pushes the interval beyond once per 30 days. Items poll at minimum monthly, forever.
- **No idle threshold stop** — items do not automatically stop polling. The only way to stop polling is a manual "Stop monitoring" action.
- **Pin override** — user can pin any item to a fixed frequency ("always poll this hourly"), overriding decay.
- **Manual disable** — "Stop monitoring" button on item detail, and as a bulk action from the list view.
- **Re-activate** — stopped items can be re-activated, which resets decay to 1.0.

This requires a backend change: update `MonitorService` to use a 30-day floor instead of the current idle-threshold stop behaviour.

### Source Defaults (updated)

| Source  | Initial frequency | Decay multiplier | Floor |
|---------|-------------------|------------------|-------|
| Reddit  | 60 min            | 1.5x             | 30 days |
| X       | 30 min            | 2.0x             | 30 days |
| GitHub  | 6 hours           | 1.5x             | 30 days |
| YouTube | 24 hours          | 2.0x             | 30 days |
| Article | 24 hours          | 2.0x             | 30 days |

---

## API Layer (`lib/knowledge.ts`)

Typed fetch wrappers for all Knowledge Base Service endpoints:

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `ingestUrl(url, opts?)` | `POST /api/knowledge/ingest` | Submit URL for ingest |
| `listItems(filters?)` | `GET /api/knowledge/items` | List with source_type, status, category, limit, offset |
| `getItem(id)` | `GET /api/knowledge/items/{id}` | Fetch single item |
| `deleteItem(id)` | `DELETE /api/knowledge/items/{id}` | Delete item |
| `searchItems(query, mode, limit?)` | `GET /api/knowledge/search` | Keyword or semantic search |
| `listSnapshots(itemId, limit?)` | `GET /api/knowledge/items/{id}/snapshots` | Monitoring snapshots |
| `listRules()` | `GET /api/knowledge/rules` | All category rules |
| `createRule(rule)` | `POST /api/knowledge/rules` | Create category rule |
| `deleteRule(id)` | `DELETE /api/knowledge/rules/{id}` | Delete category rule |
| `listSubscriptions(agentName?)` | `GET /api/knowledge/subscriptions` | Agent subscriptions |
| `setSubscription(sub)` | `POST /api/knowledge/subscriptions` | Upsert subscription |
| `deleteSubscription(agent, cat)` | `DELETE /api/knowledge/subscriptions/{agent}/{cat}` | Remove subscription |

All functions follow the existing pattern: check `content-type` header, handle errors gracefully, return typed results.

---

## Testing

- API helpers: unit tests mocking fetch for each endpoint wrapper
- Component: test view state transitions (list → detail → back preserves state)
- Search: test keyword and semantic mode switching, filter combination
- Category management: test create/rename/merge/delete flows
- Monitoring: test filter views (recent changes, active, slow), manual disable/re-activate
- Accessibility: ARIA labels on all interactive elements, keyboard navigation, screen reader compatibility

---

## Non-Goals

- Not a content editor — read-only display of ingested content
- Not a replacement for platform-specific apps (Reddit Client, YouTube Library, etc.) — those handle platform-native browsing and ingest; Library handles cross-platform management
- No inline ingest — the Library App displays and manages content, it doesn't have a URL input bar. Ingest happens via platform apps, share sheets, or the browser extension.
- No real-time updates — polling-based refresh. Pull to refresh or periodic auto-refresh.
