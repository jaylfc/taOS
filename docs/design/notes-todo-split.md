# DESIGN: Split Notes and Todo into Separate Apps (#1923)

## Summary

Split the current shared Notes/Todo implementation into two fully independent
apps with distinct data models, API surfaces, and frontend components.

**Current state:** One component (`DocsApp` in `NotesApp.tsx`) serving two app
IDs (`notes`, `todo`) via config switching (`NOTES_CONFIG` / `TODO_CONFIG`).
One API (`/api/notes`) over one store (`SharedDocsStore`) differentiated by
`kind` field (`note` vs `list`).

**Target state:** `NotesApp.tsx` + `/api/notes` + `NotesStore` for free-text
notes. `TodoApp.tsx` + `/api/todo` + `TodoStore` for checklist-oriented lists
with ordering, completion, and optional due/reminder dates.

---

## 1. Storage Design

### Decision: Split into separate stores

**Rationale:** The two use cases diverge enough that a single schema stretched
to cover both would be awkward. Todo needs per-item ordering (`position`),
completion tracking, and future due-date/reminder support. Notes will evolve
toward rich text, diagrams, and block-based content. Keeping them in one table
forces every Notes row to carry unused `done`/`position`/`due_at` columns and
every Todo row to carry block-type metadata it won't use.

The shared collaboration model (members, permissions, agent triggers, revision
history) will be **extracted into a shared base** rather than duplicated.

### 1a. TodoStore (new)

Dedicated SQLite store at `tinyagentos/todo/todo_store.py`. Schema:

```sql
CREATE TABLE todo_lists (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL
);

CREATE TABLE todo_items (
    id          TEXT PRIMARY KEY,
    list_id     TEXT NOT NULL REFERENCES todo_lists(id),
    text        TEXT NOT NULL DEFAULT '',
    done        INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    due_at      REAL,        -- optional deadline
    remind_at   REAL,        -- optional reminder timestamp
    author      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX idx_todo_items_list ON todo_items(list_id, position);

-- Share the existing member + revision tables from SharedDocsStore
-- via a shared_collaboration module (see 1c).
```

Key differences from current shared_doc_entries:
- `position` column enables drag-to-reorder
- `due_at` / `remind_at` for future due-date/reminder features
- `updated_at` tracked per-item (needed for sync/last-modified display)
- No generic `kind` column — every row is a todo item

### 1b. NotesStore (refactored from SharedDocsStore)

Keep `tinyagentos/notes/shared_docs_store.py` but rename/refactor:

- Remove `kind` column from `shared_docs` — all docs are notes now
- Remove `done` from `shared_doc_entries` — notes don't have completion
- Rename `shared_doc_entries` → `note_blocks` (optional; can defer)
- The existing entry revision history is preserved **via the shared
  collaboration module** (see 1c) — it's useful for both apps without
  duplication

Schema (existing, minus `kind` and `done`):

```sql
CREATE TABLE notes (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL
);

CREATE TABLE note_entries (
    id         TEXT PRIMARY KEY,
    note_id    TEXT NOT NULL REFERENCES notes(id),
    text       TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

-- Member + revision tables shared via shared_collaboration module
```

### 1c. Shared Collaboration Module (extracted, not duplicated)

Both stores use identical member/permission/revision patterns. Extract these
into a reusable module at `tinyagentos/collaboration/`:

```
tinyagentos/collaboration/
    __init__.py
    member_store.py   -- member CRUD, permission check, discuss_channel
    revision_store.py -- immutable revision log with diff/snapshot
    constants.py      -- VALID_PERMISSIONS, VALID_ACTIONS
```

Each store (`todo_store.py`, `notes_store.py`) composes the collaboration
tables into its own schema string and delegates member/revision operations.

**Why not one generic store:** The todo and notes row shapes differ enough
(`position`, `due_at` vs no such columns) that a single "generic doc" table
would require nullable-everything columns and runtime kind-checking. Separate
stores with shared collaboration plumbing give us type-safe schemas without
duplicating the member/revision logic.

---

## 2. API Surface

### 2a. Todo API (`/api/todo`)

New route module at `tinyagentos/routes/todo.py`, registered as an APIRouter
in `create_app()`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/todo` | List user's todo lists (non-archived) |
| POST | `/api/todo` | Create a new todo list |
| GET | `/api/todo/{list_id}` | Get list with items (ordered by position) |
| PATCH | `/api/todo/{list_id}` | Rename or archive list |
| POST | `/api/todo/{list_id}/items` | Add item (appended at end) |
| PATCH | `/api/todo/{list_id}/items/{item_id}` | Toggle done, set due date, edit text |
| DELETE | `/api/todo/{list_id}/items/{item_id}` | Delete item |
| PUT | `/api/todo/{list_id}/items/reorder` | Batch reorder: `{items: [{id, position}, ...]}` |
| GET | `/api/todo/{list_id}/members` | List members |
| POST | `/api/todo/{list_id}/members` | Add member (user or agent) |
| DELETE | `/api/todo/{list_id}/members/{type}/{id}` | Remove member |

Pydantic models:

```python
class CreateTodoListIn(BaseModel):
    title: str = ""

class PatchTodoListIn(BaseModel):
    title: str | None = None
    archived: bool | None = None

class AddTodoItemIn(BaseModel):
    text: str
    due_at: str | None = None      # ISO-8601 timestamp
    remind_at: str | None = None   # ISO-8601 timestamp

class PatchTodoItemIn(BaseModel):
    text: str | None = None
    done: bool | None = None
    due_at: str | None = None
    remind_at: str | None = None

class ReorderItemsIn(BaseModel):
    items: list[ReorderEntry]

class ReorderEntry(BaseModel):
    id: str
    position: int
```

**Reorder contract:** The reorder endpoint receives a complete ordering of
every item in the list — each `id` must appear exactly once, `position`
values must be unique (no ties), and the entire batch is applied atomically
within a single transaction. Partial or invalid payloads (missing items,
duplicate positions, unknown ids) are rejected with `400 Bad Request` and
make no changes to the stored order.

**Timestamp conversion:** `due_at` and `remind_at` arrive as ISO-8601
strings (e.g. `"2026-07-18T14:00:00Z"`) from the API. The route handler
converts them to SQLite REAL (Unix epoch seconds) via
`datetime.fromisoformat()` (Python ≥ 3.11, which this project requires,
handles the `Z` suffix natively as UTC). The store writes
the float timestamp directly into the `REAL` column, matching the existing
`created_at`/`updated_at` convention used throughout the codebase. On read,
the store returns the float; the route serializes it back to ISO-8601 for
the response model.

### 2b. Notes API (`/api/notes`)

Keep existing routes at `/api/notes` with minimal changes:

- Remove `kind` from `CreateDocIn` (all docs are notes now)
- Remove `PatchEntryIn.done` (notes don't have completion)
- Entry history endpoints preserved as-is
- Member endpoints preserved as-is

```python
class CreateNoteIn(BaseModel):     # was CreateDocIn
    title: str = ""

class AddEntryIn(BaseModel):       # unchanged
    text: str

class EditEntryTextIn(BaseModel):  # unchanged
    text: str
```

### 2c. Agent tools

| Current Tool | After (rename) | Purpose |
|------|---------|-------|
| `notes_list_shared_docs` | → `notes_list_docs` | Lists the agent's notes |
| `notes_add_entry` | → `notes_add_entry` | Appends to a note |
| `notes_set_done` | → **removed** from notes | Notes don't have done |
| (new) `todo_list_lists` | — | Lists the agent's todo lists |
| (new) `todo_add_item` | — | Appends a todo item |
| (new) `todo_set_done` | — | Toggles completion on a todo item |

---

## 3. Component Split

### 3a. Current: One component, two configs

`NotesApp.tsx` (1177 lines) exports both `NotesApp` and `TodoApp`. They share
`DocsApp`, `NoteDetailPane`, `EntryRow`, `SharePanel`, `EntryHistoryPanel`,
and `CreateNoteForm` — differentiated only by the `DocKindConfig` object.

### 3b. Target: Two separate component trees

```
desktop/src/apps/
    NotesApp.tsx          ← free-text notes component
    NotesApp.test.tsx
    TodoApp.tsx           ← checklist-oriented todo component
    TodoApp.test.tsx
```

**Shared UI primitives extracted to `desktop/src/apps/notes-shared/`:**

- `MemberBadge.tsx` — unchanged
- `SharePanel.tsx` — shared via members API (different endpoint per app)
- `EntryHistoryPanel.tsx` — revision viewer, unchanged internally

**NotesApp specifics:**
- No done checkboxes
- No reorder drag handles
- Textarea-based entry input (existing)
- Future: rich text editor, diagram canvas, link embeds

**TodoApp specifics:**
- Checkbox per item (Square/CheckSquare, optimistic toggle)
- Drag handles for reorder (future: `@dnd-kit` or native HTML5 drag)
- Due date display + date picker
- "Add task" input bar (single-line + Enter to add)
- Visual separation: completed items moved to bottom or collapsible section

### 3c. App registry

No changes to `app-registry.ts` needed — the two app IDs (`notes`, `todo`)
already exist and will continue to lazy-import their respective components:

```typescript
// Before (both from same chunk):
{ id: "notes", component: () => import("@/apps/NotesApp").then(m => ({ default: m.NotesApp })) }
{ id: "todo",  component: () => import("@/apps/NotesApp").then(m => ({ default: m.TodoApp })) }

// After (separate chunks):
{ id: "notes", component: () => import("@/apps/NotesApp").then(m => ({ default: m.NotesApp })) }
{ id: "todo",  component: () => import("@/apps/TodoApp").then(m => ({ default: m.TodoApp })) }
```

---

## 4. Migration Plan

### 4a. Database migration

A one-time migration script at `tinyagentos/migrations/migrate_notes_todo_split.py`
(or as a store `_post_init` migration):

**Step 1:** Create `todo_lists` and `todo_items` tables.

**Step 2:** Copy existing `shared_docs` with `kind = 'list'` into `todo_lists`:
```sql
INSERT INTO todo_lists (id, owner_user_id, title, created_at, updated_at, archived_at)
SELECT id, owner_user_id, title, created_at, updated_at, archived_at
FROM shared_docs WHERE kind = 'list';
```

**Step 3:** Copy entries:
```sql
INSERT INTO todo_items (id, list_id, text, done, position, author, created_at, updated_at)
SELECT e.id, e.doc_id, e.text, e.done,
       (SELECT COUNT(*) FROM shared_doc_entries e2
        WHERE e2.doc_id = e.doc_id AND e2.created_at <= e.created_at) - 1,
       e.author, e.created_at, e.created_at
FROM shared_doc_entries e
JOIN shared_docs d ON d.id = e.doc_id
WHERE d.kind = 'list';
```

`position` is derived from `created_at` order — items retain their existing
chronological order after migration.

**Step 4:** Copy members for migrated lists into new todo_members table.

**Step 5:** Do NOT delete the source data — leave `shared_docs` rows in place
as a safety net. The migration is additive. A future cleanup PR can remove
kind=list rows after the split is stable.

**Step 6:** Remove `kind` column from `shared_docs` (or set all remaining to
`note`). Drop the `done` column from `shared_doc_entries` after confirming
no Todo consumers remain on the old tables.

### 4b. Rollout strategy

1. **Phase 1 (C1):** Backend split — `TodoStore`, `routes/todo.py`, migration
   script. Old `/api/notes` still serves both kinds during transition.
2. **Phase 2 (C2):** Frontend split — `TodoApp.tsx` hits new `/api/todo`
   endpoints. `NotesApp.tsx` hits `/api/notes`. Both apps fully functional.
3. **Phase 3 (C3):** Agent tools — `todo_list_lists`, `todo_add_item`,
   `todo_set_done`. Remove `notes_set_done`.
4. **Phase 4 (C4):** Cleanup — remove `kind` from Notes API, drop kind=list
   support from `/api/notes`, remove old `shared_doc_entries.done` column.

---

## 5. App Registry Changes

Minimal — the two app IDs (`notes`, `todo`) remain but now point to separate
component chunks:

```typescript
{ id: "notes", name: "Notes", icon: "sticky-note", category: "platform",
  component: () => import("@/apps/NotesApp").then(m => ({ default: m.NotesApp })),
  defaultSize: { w: 860, h: 620 }, minSize: { w: 520, h: 400 },
  singleton: true, pinned: false, launchpadOrder: 16.8 },

{ id: "todo", name: "Todo", icon: "list-checks", category: "platform",
  component: () => import("@/apps/TodoApp").then(m => ({ default: m.TodoApp })),
  defaultSize: { w: 860, h: 620 }, minSize: { w: 520, h: 400 },
  singleton: true, pinned: false, launchpadOrder: 16.85 },
```

---

## 6. Child Task Breakdown (C1–C4)

### C1: Backend — TodoStore + routes/todo.py + migration
**Assignee:** `skald-engineer-taos`
- Create `tinyagentos/todo/todo_store.py` with TodoStore (BaseStore subclass)
- Create `tinyagentos/routes/todo.py` with full CRUD + reorder endpoints
- Register router in `app.py`'s `create_app()`
- Create migration script or `_post_init` migration
- Write `tests/todo/test_todo_routes.py`

### C2: Frontend — TodoApp.tsx + NotesApp refactor
**Assignee:** `skald-engineer-taos`
- Create `desktop/src/apps/TodoApp.tsx` (checklist UI, drag handles, due dates)
- Extract shared primitives to `desktop/src/apps/notes-shared/`
- Refactor `NotesApp.tsx` to remove Todo config, keep Notes only
- Update app-registry imports
- Write/update tests

### C3: Agent tools — todo tools + notes cleanup
**Assignee:** `skald-engineer-taos`
- Create `tinyagentos/tools/todo_tools.py` with `todo_list_lists`, `todo_add_item`, `todo_set_done`
- Remove `notes_set_done` from `notes_tools.py`
- Register new tools in tool registry
- Write `tests/todo/test_todo_tools.py`

### C4: Cleanup — remove kind, drop done column, final consolidation
**Assignee:** `skald-engineer-taos`
- Remove `kind` from Notes API (CreateDocIn, routes, store)
- Drop `done` column from `shared_doc_entries` (schema migration)
- Remove kind=list filtering from Notes frontend
- Update integration tests
- Run full test gate

---

## 7. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Migration breaks existing lists | Additive-only migration; old data preserved; both APIs run side-by-side during transition |
| Frontend regressions | Separate test files for each app; existing tests pass before merge |
| Agent tools silently break | Tool registry registration is atomic; old tool stays registered until C4 explicitly removes it |
| Duplicated collaboration logic | Extracted to shared `collaboration/` module in C1; both stores compose it |
