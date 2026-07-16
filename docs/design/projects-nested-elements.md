# Projects rework: one project, nested typed elements (founding design)

Status: DECIDED architecture (four locked decisions below), DRAFT data model
and slice plan for Jay review. Author: taOS-dev.

## Why this exists

The current Projects app models every effort as a flat sibling project, and
that flow is wrong. Jay's own board is the proof: taOS, taOSmd, and taOS
Website exist today as three flat sibling projects, but they are really three
elements of ONE taOS project. The flat model forces the split, then punishes
it: three boards to check instead of one, three member lists to keep in sync,
three a2a channels for agents that are actually one team, and no place where
the whole effort is visible at once.

Most users will want the opposite shape: one project with multiple nested
elements. The canonical example is a t-shirt business. That is one project,
and it would contain the designs themselves, the website, marketing material,
and the business docs and plans. Nobody thinks of those as four businesses;
they are four parts of one thing, worked by one team, discussed in one place.

This document restructures the Projects app around that shape: a project
becomes a container, and the things inside it become first-class ELEMENTS with
their own scoped views, optional types, and optional owners, while everything
that must stay whole (the board, the members, the a2a channel, the memory
shelf) stays whole at the project level.

## The four locked decisions

Jay chose these explicitly. They are the foundation and are not up for
re-litigation in review; everything else in this document serves them.

1. **Hybrid element model.** A project is essentially a label, a container.
   Adding an element defaults to a GENERIC element that behaves like a generic
   nested project: it gets its own scoped view of the board, canvas, and files
   via tags. TYPED elements are optional refinements on top of generic, with
   tailored views and default app associations: code repo, website, design
   collection, docs, marketing, and a business-planning type tailored for
   business plans and financial forecasts. The type system is extensible: new
   types are addable without a schema migration, because a type is metadata
   plus view hints, never a table shape.
2. **Project-level backbone, element-level tags, plus whole-element
   assignment.** There is ONE task board, ONE member list, ONE a2a channel,
   and ONE memory shelf per PROJECT. This preserves the 1 shelf : 1 project
   cardinality that #774 builds on. Tasks, files, and canvas items carry an
   ELEMENT TAG used for filtering and for the scoped element views. On top of
   the tags, an element can be assigned whole to a member or agent, for
   example the website element assigned to a web-design-customised agent.
   Assignment means items tagged with that element route to that assignee by
   default, and the element view highlights their ownership.
3. **Migration is a "group into project" action, and it is reversible.** The
   user selects sibling projects and they become elements of a new or existing
   parent. Boards and members merge, and element tags are applied
   automatically: each absorbed project's tasks get its element tag. The
   reverse operation is "promote element to standalone project". Jay's taOS +
   taOSmd + taOS Website trio is the canonical first migration.
4. **External agent binding: both, element optional.** Agent tokens keep
   binding to the PROJECT. There is zero change to the just-shipped
   `project_tasks` scope and the consent picker. An OPTIONAL element claim can
   narrow scope later: the token claim shape is designed now so the addition
   is purely additive, and the consent-picker element field is roadmap.

## Non-goals

- **No second board.** Elements never get their own task table, member table,
  channel, or shelf. An element view is a filter over project data, not a
  container of copies.
- **No nested elements inside elements.** Depth is capped at one level in v1
  (Open question 1 records the recommendation). Hierarchy within a board
  already exists via `parent_task_id`.
- **No forced migration.** A project with zero elements looks and behaves
  exactly as today. Nothing is auto-grouped, no default element is minted for
  existing projects (Open question 3).
- **No change to the external-agent consent flow in v1.** The element claim
  is a documented shape, not a shipped feature.
- **No commercial or packaging concerns.** Product structure only.

## Current state (verified in code)

What this design builds on, all already in the tree:

- **Projects store** (`tinyagentos/projects/project_store.py`): `projects`,
  `project_members`, `project_activity` tables in `data_dir/projects.db`.
  `_post_init` already demonstrates the additive-ALTER migration pattern this
  design reuses (try ALTER, swallow the duplicate-column error).
- **Task store** (`tinyagentos/projects/task_store.py`): `project_tasks`
  keyed by `project_id` with `parent_task_id`, labels, assignee, claim
  lifecycle, `task_relationships`, `task_comments`, and the `ready_tasks`
  view. Shares the same `projects.db` file.
- **Canvas store** (`tinyagentos/projects/canvas/store.py`):
  `project_canvas_elements` keyed by `project_id`, also in `projects.db`.
  Note the naming collision handled in Terminology below.
- **One database file.** `tinyagentos/app.py` constructs `ProjectStore`,
  `ProjectTaskStore`, and `ProjectCanvasStore` against the same
  `data_dir/projects.db`. The group-into-project migration can therefore be a
  single SQLite transaction, not a cross-store saga.
- **Routes** (`tinyagentos/routes/projects.py`): project + member CRUD, task
  CRUD and lifecycle (claim/release/close/reopen), comments, relationships,
  audit, SSE events. `_authorize_task_actor` resolves a session owner/admin
  OR an external agent holding a `project_tasks` grant bound to this project,
  with existence-hiding 404 semantics.
- **Agent token scope** (`tinyagentos/agent_token_auth.py`):
  `check_agent_scope_for_project` verifies the EdDSA registry JWT, the active
  grant, AND that both the token's `project_id` claim and the grant's own
  `project_id` match the requested project. This is the seam the optional
  element claim extends.
- **A2A channel invariant** (`tinyagentos/projects/a2a.py`): exactly one
  chat channel per active project with `name="a2a"`, member-synced from
  `project_members` by `ensure_a2a_channel`, serialized per project.
- **Folder mirror** (`tinyagentos/projects/folders.py`): each project mirrors
  to `projects_root/<slug>/` with `memory/`, `canvas/`, `files/` subfolders
  and a `project.yaml`. The Files tab mounts `FilesApp` with
  `rootPath="project:<slug>"` (`desktop/src/apps/ProjectsApp/ProjectWorkspace.tsx`).
- **Board UI** (`desktop/src/apps/ProjectsApp/board/`): `Filters` type in
  `types.ts`, pure `applyFilters` in `boardFiltering.ts`, filter popover in
  `BoardFilters.tsx`, toolbar in `BoardToolbar.tsx`. The element filter bar
  slots into this existing architecture.
- **Members UI** (`desktop/src/apps/ProjectsApp/ProjectMembers.tsx`): native,
  clone, and external members with role/lead controls; the natural home for
  element assignment.
- **Shelf registry direction** (#774): 1 shelf : 1 project is the working
  cardinality, with link-before-merge reconciliation. This design must not
  break it, and does not: elements never mint shelves.
- **External agent invite flow**
  (`docs/design/external-agent-project-invite.md`, on
  `feat/account-slice-3`): invites are project-bound and force-include
  `project_tasks`. The element claim below is designed to compose with it,
  not change it.
- **Hub business pages** (`docs/design/hub-social-network-foundation.md`):
  business profiles exist as a rendering `kind` on the hub. The
  business-planning element type is the in-OS counterpart and the roadmap
  notes the tie-in.

## Terminology: project elements vs canvas elements

The codebase already uses "element" for canvas rows (`project_canvas_elements`,
`element-to-excalidraw.ts`). To avoid a permanent ambiguity:

- **Element** in this document, in new code, and in all UI copy means a
  PROJECT element (the new record).
- Canvas rows are called **canvas items** in UI copy going forward. The
  `project_canvas_elements` table and its API paths are not renamed (churn
  without benefit); new code comments should say "canvas item" where
  confusion is possible.
- The tag column added to `project_canvas_elements` is named `element_id` and
  refers to the project element. The canvas item's own key remains `id`.

## Data model

### The element record

New table in `projects.db`, owned by a new `tinyagentos/projects/element_store.py`
(same `BaseStore` pattern as its siblings):

```sql
CREATE TABLE IF NOT EXISTS project_elements (
    id          TEXT PRIMARY KEY,           -- new_id("elm")
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,              -- files subfolder + display key
    type        TEXT NOT NULL DEFAULT 'generic',
    description TEXT NOT NULL DEFAULT '',
    assignee_id TEXT,                       -- a project_members.member_id, or NULL
    settings    TEXT NOT NULL DEFAULT '{}', -- JSON: view hints, type config, origin
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    archived_at REAL,
    UNIQUE (project_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_elements_project ON project_elements(project_id);
```

- `slug` follows the project slug rules (`_SLUG_RE` in `routes/projects.py`),
  unique per project, used for the element's files subfolder.
- `assignee_id` references a current project member (native agent id, clone
  id, or external handle, exactly the `project_members.member_id` vocabulary).
  Validated at write time; SQLite FK enforcement is not relied on.
- `settings` carries per-type configuration (for example `{"url": ...}` for a
  website element, `{"repo_url": ...}` for a code element) and, when the
  element was created by group-into-project, an `origin` snapshot (see
  Migration mechanics).
- `archived_at` gives elements the same soft-archive posture as projects.

### Element tags on items (nullable, untagged = project-level)

- **Tasks**: `project_tasks` gains `element_id TEXT` (nullable) plus
  `CREATE INDEX idx_tasks_element ON project_tasks(project_id, element_id)`.
  Added to `TASK_SCHEMA` for fresh installs and via the `_post_init` ALTER
  pattern for existing databases.
- **Canvas items**: `project_canvas_elements` gains `element_id TEXT`
  (nullable), same dual schema+ALTER treatment.
- **Files**: the filesystem is the store, so the tag is a subfolder. An
  element owns `projects_root/<project-slug>/files/<element-slug>/`, created
  by `ensure_project_layout` extension on element create. Files directly
  under `files/` are project-level. If `files/<element-slug>/` already exists
  as a user-made folder, the element adopts it rather than erroring.

A NULL tag always means project-level. Untagged items appear in the project
overview and in every "All" view; they never disappear into an element.

### The type registry (extensible without migration)

`type` is a free string column. The registry of KNOWN types is code-level
metadata, one source per side:

- `tinyagentos/projects/element_types.py`: `{type: {label, icon, default_tabs,
  app_hints}}` for anything the server needs (folder seeding, future
  templates).
- `desktop/src/apps/ProjectsApp/elements/types.ts`: the same map for
  rendering (card icon, tab order, default app associations).

Rules that make it extensible:

- The server shape-checks `type` as a slug-like string and stores it. It does
  NOT validate against the registry, so a newer client (or a future package)
  can introduce a type without touching the server.
- Any type the client does not recognise renders as generic. Generic is the
  behavioural floor for every element, typed or not.
- Adding a type is therefore: one registry entry per side, zero schema work.
  Per-type configuration lives in `element.settings`.

Initial registry:

| type       | label               | tailored view and default app associations |
|------------|---------------------|---------------------------------------------|
| `generic`  | Element             | Board, canvas, files, all element-filtered. The default for every new element. |
| `code`     | Code repo           | Board-first. `settings.repo_url`; card surfaces a repo link and (later) the coding-session launcher. |
| `website`  | Website             | `settings.url`; card offers open-in-Browser preview; board + canvas tabs. |
| `design`   | Design collection   | Canvas-first: the element-filtered canvas is the landing tab; files for exports. |
| `docs`     | Docs                | Files-first; pairs with shared docs. |
| `marketing`| Marketing           | Canvas + files; campaign material and copy live here. |
| `business` | Business planning   | Tailored for business plans and financial forecasts: files-first with plan/forecast document templates seeded into its files folder. Roadmap: feeds the project's business page on hub.taos.my (the `kind: "business"` profile in the hub design). |

Changing an element's type later is a metadata edit (PATCH), never a
migration; the tags and folder do not move.

## API surface

All element routes are owner-gated exactly like the member routes (session
owner/admin via `_get_owned_project` semantics, existence-hiding 404).
External agent tokens get no new mutation surface in v1.

### Element CRUD, nested under the project

- `POST /api/projects/{pid}/elements`
  `{name, slug?, type?, description?, assignee_id?, settings?}`.
  Slug defaults to slugified name; 409 on duplicate slug. Creates the files
  subfolder best-effort (the `_mirror` posture: DB row is authoritative).
- `GET /api/projects/{pid}/elements` returns
  `{items: [element + counts {open_tasks, canvas_items}]}` so the overview
  grid renders in one call.
- `GET /api/projects/{pid}/elements/{eid}`
- `PATCH /api/projects/{pid}/elements/{eid}`
  `{name?, type?, description?, assignee_id?, settings?}`. `assignee_id` must
  be a current project member or null. Slug rename is deliberately excluded
  from v1 (it implies a folder move; Open question 5).
- `POST /api/projects/{pid}/elements/{eid}/archive` sets `archived_at`; tags
  are retained, the element leaves the grid and filter bar defaults.
- `DELETE /api/projects/{pid}/elements/{eid}` only when nothing carries the
  tag; otherwise 409 with `{open_tasks, total_tasks, canvas_items}` and the
  caller must pick `?mode=untag` (null all tags, folder becomes a plain
  folder) or use archive. The UI leads with archive.

### Tags on task routes

- `CreateTaskIn` gains `element_id: str | None`, validated to name a
  non-archived element of this project (400 otherwise). Task creation stays
  session-only, as today.
- `UpdateTaskIn` gains `element_id` (with an explicit `"none"` sentinel or a
  present-null convention chosen in slice 1) so a session owner can move a
  task between elements. The agent PATCH exclusion from #1774 is unchanged.
- `GET /api/projects/{pid}/tasks?element_id=<eid>` filters; `element_id=none`
  returns untagged tasks. Same on `/tasks/ready`. These queries pass through
  `_authorize_task_actor` untouched: a project-bound agent token may filter
  by element, since element is a view of data it already reads.
- Task responses include `element_id`; the Beads JSONL snapshot
  (`tinyagentos/projects/beads_format.py`) carries it for round-trip
  fidelity.

### Tags on canvas routes

- `POST /api/projects/{pid}/canvas/elements` accepts `element_id`;
  `GET /api/projects/{pid}/canvas/elements?element_id=` filters, with the
  same `none` sentinel.

### Group and promote

- `POST /api/projects/group`
  `{source_project_ids: [...], target: {project_id} | {name, slug}}`.
  Top-level (not nested) because it may create the parent. Returns the parent
  project plus the created elements. Semantics in Migration mechanics.
- `POST /api/projects/{pid}/elements/{eid}/promote` `{name?, slug?}`.
  Returns the new standalone project. Semantics in Promote below.

## Whole-element assignment

Assignment is decision 2's second half: an element can belong to someone.

- `element.assignee_id` names one project member (human-side member support
  arrives with multi-user; today members are agents, so in practice this is
  "the website element is the web-design agent's").
- **Default routing, materialized at write.** When a task is created with an
  `element_id` and no explicit `assignee_id`, the element's assignee is
  written as the task's `assignee_id`. This happens at create time, in the
  route, and is visible in the response. It is not a dynamic view: changing
  the element's assignee later does not rewrite existing tasks. The Members
  UI offers an explicit bulk action ("reassign this element's open, unclaimed
  tasks to the new owner") for when that is wanted, and it logs activity.
- **Claim lifecycle unchanged.** Default assignment is a hint for the board
  and for agents polling ready tasks; it does not claim the task, and the
  one-active-task-per-agent invariant in `claim_task` is untouched.
- The element view highlights ownership: the assignee is pinned on the
  element card and header, and their tasks are visually primary in the
  scoped board.
- Removing a member from the project clears `assignee_id` on any element they
  owned, with an activity entry, so no element points at a ghost.

## UI/UX flows

### Project home becomes an element overview grid

`ProjectWorkspace.tsx`'s "workspace" tab becomes the element overview when a
project has elements (zero-element projects keep today's workspace pane
untouched):

- A grid of element cards: type icon, name, assignee chip, open task count,
  a small recent-activity line. Cards come from the counts-included list
  endpoint.
- One additional fixed card, "Project" (the untagged/project-level view), so
  cross-cutting items always have a visible home.
- An "Add element" tile opens the element creation dialog: name, then a type
  picker that defaults to generic, with the typed options presented as
  refinements ("give this element a tailored view"), plus optional assignee.

### Navigation: project, then element, then content

`ProjectsApp/index.tsx` keeps the project list pane. Inside the workspace,
clicking an element card drills into the element view: the same tab set
(board, canvas, files, and the rest) rendered with the element filter
pre-applied and the tab order taken from the type's view hints (a design
element lands on canvas, a docs element on files). A breadcrumb
(`project name / element name`) returns to the grid. The element id rides the
window URL params next to the existing `task` param so deep links and
open-in-new-window work per element.

### Creation flow

`CreateProjectDialog.tsx` becomes two steps. Step one is unchanged: name,
slug, description. Step two, "Add elements", is optional and skippable: a
repeatable row of name + type picker (defaulting to generic). Skipping yields
a project identical to today's. The t-shirt flow reads: create "T-Shirt
Business", add "Designs" (design collection), "Website" (website),
"Marketing" (marketing), "Business plan" (business planning).

### Kanban element filter bar

The board gains a persistent chip row (not buried in the filter popover,
elements are the primary axis): `All | <element chips...> | Project-level`,
rendered by a new `board/ElementFilterBar.tsx` inside `BoardToolbar.tsx`.
State is `Filters.elementId: string | "none" | null` in `board/types.ts`,
applied in `boardFiltering.ts` (pure, testable, like every existing filter).
Element-scoped views render the same board with the chip pinned and hidden.
Task cards show a small element badge when the board is unfiltered.

### Members and assignment UI

`ProjectMembers.tsx` gains an "Element ownership" section: each element with
an assignee picker (project members only), and per member a summary of owned
elements. The same picker appears on the element card menu. The bulk
reassign action lives here, behind an explicit confirm that states the task
count.

## Cross-cutting systems

- **A2A channel: stays one per project.** `ensure_a2a_channel`'s invariant is
  untouched. The team is project-wide; element context in conversation is
  social convention (mention the element), not machinery. When grouping
  merges member sets, the existing member-sync in `ensure_a2a_channel`
  already converges the channel; the absorbed projects' channels are
  archived, exactly like `delete_project` archives channels today.
- **Memory shelf: stays one per project (#774 preserved).** Elements never
  mint shelves, so the 1 shelf : 1 project cardinality and the registry
  design in #774 hold. Element granularity inside the shelf uses the existing
  qmd tag pattern (`project:{id}` today) extended with an optional
  `element:{id}` tag on ingest, so element views can bias memory search
  without any shelf-layer change. Absorbed projects' shelves are handled by
  the #774 LINK operation, see Migration mechanics.
- **External agent scope: unchanged, with an additive element claim
  documented now.** Today: token carries a top-level `project_id` claim, the
  grant row carries `project_id`, and `check_agent_scope_for_project`
  requires both to match. The additive shape, fixed now so nothing built
  today needs rework:
  - Token: an OPTIONAL top-level `element_id` claim next to `project_id`.
  - Grant: an optional `element_id` column on the grant row, mirroring how
    `project_id` lives on both token and grant (defense in depth).
  - Semantics when present: `project_tasks` narrows to tasks whose
    `element_id` equals the claim. Reads return only that element's tasks;
    lifecycle actions on any other task get the existence-hiding 404.
    Untagged tasks are NOT included in a narrowed view (least privilege; an
    element-scoped agent sees its element, nothing more).
  - Absent claim = today's behaviour, whole project. Every existing token
    remains valid unmodified.
  - Roadmap, not v1: the consent-picker element field (an optional element
    dropdown after the project picker) and invite-flow support. The invite
    design's force-include of `project_tasks` composes cleanly: an element
    claim narrows that scope, it never adds one.

## Migration mechanics: group into project

`POST /api/projects/group` is the flat-to-nested bridge.

### Validation (fail before touching anything)

- Caller owns (or is admin over) every source and the target.
- Sources are active, distinct, not the target, and not already absorbed.
- **External-token guard:** if any source project has active project-bound
  agent grants (`project_tasks` or invite-minted scopes), the group call
  returns 409 listing the affected agents. Those tokens are bound to a
  project id that is about to stop being a working project, and silently
  breaking them is worse than asking. The user revokes or re-invites against
  the parent (grant migration with consent is roadmap). This guard is why the
  canonical taOS trio migration happens before, or in coordination with,
  external agent invites on those projects.

### The transaction

All three stores share `projects.db`, so steps 2 through 6 run in ONE SQLite
transaction; either the whole group lands or none of it.

1. Resolve the target: existing project, or create `{name, slug}` first
   (normal create path, so the parent gets its folder, activity, and a2a
   channel).
2. For each source project, mint an element in the parent:
   `name = source.name`, `slug = source.slug` (deduped on collision, see Edge
   cases), `type = 'generic'` (the user refines types afterwards; guessing is
   worse than defaulting), and `settings.origin = {project_id, slug,
   member_snapshot, shelf, grouped_at}` capturing exactly what reversal
   needs.
3. Repoint and tag tasks:
   `UPDATE project_tasks SET project_id = :parent, element_id = :elm WHERE
   project_id = :source`, and the same repoint on
   `task_relationships.project_id`. Comments key on `task_id` and follow for
   free. Claims, statuses, priorities, and parent links ride along untouched.
4. Repoint and tag canvas items the same way. (Spatial note: each absorbed
   canvas arrives as its own element-filtered layer; the unfiltered project
   canvas may overlap visually until items are moved. Stated, accepted.)
5. Member union: insert each source member into the parent with
   `ON CONFLICT(project_id, member_id) DO NOTHING`. On conflict the parent's
   existing row wins (role, lead flag, canvas permission), because the parent
   is the surviving context.
6. Archive each source: `status = 'archived'`,
   `settings.absorbed_into = parent_id`. Archived, never deleted: the row is
   the tombstone that promote and audit read.
7. Commit.

### Post-commit, best-effort (the `_mirror` posture)

- Folders: move `projects_root/<source-slug>/files/` to
  `projects_root/<parent-slug>/files/<element-slug>/`; tombstone-rename the
  remainder of the source folder the way `delete_project` does. Disk failure
  logs a warning and never rolls back the DB.
- A2A: archive each source's a2a channel; run `ensure_a2a_channel(parent)` so
  the merged member set converges.
- Memory: apply #774's LINK, not merge: the element's `settings.origin.shelf`
  records the source's shelf binding, and the project registry repoints the
  source's match keys (git remote, folder) at the parent project. Holdings
  merge is deferred exactly as #774 defers it.
- Beads: `mark_dirty` on parent and each source.
- Activity: `project.grouped` on the parent (with source ids),
  `project.absorbed` on each source, `element.created` per element.

### Reversal: promote element to standalone project

`POST /api/projects/{pid}/elements/{eid}/promote`:

- Creates a NEW project (new id), reusing the element's origin slug and name
  when recorded and free, otherwise the element's own slug/name. The old
  archived source project is not resurrected: its id accumulated stale
  references (activity, revoked grants) while absorbed, and resurrecting ids
  is how registries rot. `settings.origin` on the archived source and the
  new project's activity entry cross-link the history.
- Reverses the tag in one transaction: tasks, relationships, and canvas items
  `WHERE element_id = :eid` move to the new project and their `element_id`
  is nulled.
- Members: restores `settings.origin.member_snapshot` when the element came
  from a group action; for born-in-project elements, the dialog presents the
  parent's member list pre-checked for the caller to prune. Members added to
  the parent after grouping stay in the parent (the union cannot be
  un-unioned automatically, and guessing would remove someone's access).
- Folder moves back out to `projects_root/<new-slug>/files/`, best-effort.
- The element row is deleted, the new project gets folder, yaml, and a2a
  channel via the normal create path, and both sides log activity.

Group then promote is therefore a true round trip for items and tags, an
exact round trip for members when the element came from grouping, and an
explicit-choice round trip otherwise. That honesty is the design.

## Edge cases, honestly

- **Task moved between elements.** Session PATCH of `element_id`. The
  assignee does NOT retroactively re-route: an explicit move keeps the
  current assignee, and the task modal offers "assign to <new element
  owner>?" as a one-click follow-up. The move lands in the board audit trail.
  Cross-element `blocks` relationships and parent/child links are legal and
  common (one board, one dependency graph); the board renders the element
  badge on both ends so a cross-element block is visible.
- **Element deleted with tagged tasks.** Bare DELETE is refused (409 with
  counts). `mode=untag` nulls every tag in one transaction and leaves the
  files folder as a plain project folder (bytes are never deleted by an
  element operation). Archive is the UI-default alternative and keeps tags
  for un-archive. Hard delete stays reserved for empty elements.
- **Dangling tags.** If an untag race or manual DB edit leaves a task
  pointing at a missing element, it renders as project-level with a warning
  badge, and list queries treat unknown `element_id` as untagged. No crash,
  no hidden task.
- **Name collisions on merge.** Element slugs are unique per project. A
  source slug colliding with an existing element (or a second source with the
  same slug) gets a numeric suffix (`website`, `website-2`); names may
  collide freely (names are labels, slugs are keys). The group response
  reports any renames so the user sees them immediately.
- **Grouping the parent into itself, or an archived source.** 400, from the
  validation step.
- **Assignee leaves the project.** `remove_member` clears their element
  assignments with activity entries (see Assignment).
- **Zero-element projects.** The back-compat invariant: no elements means no
  grid, no filter bar, no behaviour change anywhere. Existing tests must pass
  unmodified against a tagless project.
- **Agent tokens during group.** Covered by the validation guard above:
  blocked with guidance rather than silently broken.
- **Canvas terminology.** Covered in Terminology; the `element_id` column on
  `project_canvas_elements` refers to the project element.

## Canonical first migration

Jay's own taOS + taOSmd + taOS Website trio is the acceptance test for slice
6: group the three into a new "taOS" parent (the existing "taOS" name is one
of the sources, so the parent takes the name and the source slug dedups),
verify the merged board with three element tags, the merged member list, one
a2a channel, element-filtered views per element, and then promote one element
back out and verify the round trip. This migration runs on the dev box first,
then on the Pi, and the design is not "done" until that real migration has
been executed and screenshotted.

## Slice plan

Numbered, PR-sized, ordered. Slices 1 to 4 are bounded and specified tightly
enough for external CLI coding agents to implement from this text. Slices 5
to 7 are maintainer-review-required: 5 and 6 are called out by the locked
decisions as the routing and migration cores, and 6/7 move user data.

1. **Element store + CRUD + task tags (backend).**
   New `tinyagentos/projects/element_store.py` (schema above, CRUD,
   counts query). `tinyagentos/projects/task_store.py`: `element_id` column
   (schema + `_post_init` ALTER), create/update/list filter support.
   `tinyagentos/routes/projects.py`: element CRUD routes, `element_id` on
   `CreateTaskIn`/`UpdateTaskIn`/list/ready with validation, archive/delete
   modes. `tinyagentos/app.py`: store wiring.
   `tinyagentos/projects/beads_format.py`: carry `element_id`.
   Tests: `tests/projects/test_element_store.py` (new),
   `tests/test_routes_projects.py` additions (CRUD, tag validation, 409
   delete, `element_id=none`), `tests/test_routes_projects_agent_tasks.py`
   addition proving an agent token can filter by element with zero auth
   change. Bounded, external-agent-friendly.
2. **Kanban element filter bar (frontend).**
   `desktop/src/lib/projects.ts`: element client API + `element_id` on task
   types. `board/types.ts`: `Filters.elementId`. `board/boardFiltering.ts`:
   pure filter. New `board/ElementFilterBar.tsx` rendered from
   `board/BoardToolbar.tsx`; element badge on `board/TaskCard.tsx`; wiring in
   `board/useBoardData.ts`. Tests in
   `desktop/src/apps/ProjectsApp/board/__tests__/`. Bounded,
   external-agent-friendly.
3. **Creation flow + element overview grid + navigation (frontend).**
   New `desktop/src/apps/ProjectsApp/elements/` (`types.ts` registry,
   `ElementGrid.tsx`, `ElementCard.tsx`, `ElementCreateDialog.tsx`).
   `CreateProjectDialog.tsx`: optional step two. `ProjectWorkspace.tsx`:
   grid-when-elements, element drill-in with pre-applied filter and
   type-driven tab order, breadcrumb, URL param. Tests alongside plus a
   zero-element regression test asserting today's workspace is untouched.
   Bounded, external-agent-friendly.
4. **Element-scoped canvas + files.**
   `tinyagentos/projects/canvas/store.py` + `tinyagentos/routes/project_canvas.py`:
   `element_id` column, create/list filter. `tinyagentos/projects/folders.py`
   + element create route: files subfolder (with adopt-existing).
   `ProjectWorkspace.tsx`: element Files tab mounts `FilesApp` rooted at the
   element subfolder; `canvas/CanvasView.tsx` honours the element filter.
   Tests: `tests/projects/` canvas store additions,
   `tests/test_routes_project_canvas.py`, `tests/test_project_folders.py`.
   Bounded, external-agent-friendly.
5. **Element assignment + default routing (maintainer-review-required).**
   Assignee validation on element PATCH; create-task default-assignee
   materialization in `routes/projects.py`; `remove_member` clearing hook;
   bulk reassign endpoint + activity entries. `ProjectMembers.tsx` element
   ownership section; assignee chip + ownership highlight on
   `ElementCard.tsx` and the element board header. Review focus: the
   materialize-at-create semantics and the bulk action's blast radius.
6. **Group-into-project (maintainer-review-required).**
   New `tinyagentos/projects/grouping.py` (validation, the single
   transaction, post-commit folder/a2a/shelf-link/beads steps),
   `POST /api/projects/group` in `routes/projects.py`, the agent-grant 409
   guard. UI: multi-select in `ProjectList.tsx` + new
   `GroupProjectsDialog.tsx`. Tests must cover mid-transaction failure
   (nothing moved), slug dedup, member-union conflict rules, and the token
   guard. Acceptance: the canonical taOS trio migration, executed for real.
7. **Promote to standalone (maintainer-review-required).**
   Promote in `grouping.py` + route + `ElementCard.tsx` action with the
   member picker. Tests: group-then-promote round trip (items exact, members
   exact for origin-snapshot elements), born-in-project promote.
8. **Roadmap, design-gated (not scheduled).** The optional `element_id` token
   claim + grant column and the narrowed `project_tasks` semantics
   (`agent_token_auth.py`), the consent-picker element field, invite-flow
   element support, and grant migration on group. Each rides the shapes fixed
   in this document and needs its own security pass before code.

Ordering: 1 strictly first; 2, 3, 4 in any order after 1; 5 after 1 and 3;
6 after 1 (UI parts after 3); 7 after 6.

## Open questions for Jay (each with a recommendation)

1. **Element nesting depth: cap at one level?** Recommendation: yes, hard cap
   at one level in v1. Elements inside elements would recreate the flat-vs-
   nested problem one layer down and force every tag, filter, folder, and
   scope decision in this document to become recursive. `parent_task_id`
   already expresses hierarchy inside a board, and a project that genuinely
   contains projects is what group-into-project's target-an-existing-parent
   path is for. Revisit only with a concrete user shape that two levels
   cannot express.
2. **Can an element belong to two projects?** Recommendation: no. One
   `project_id` per element keeps tags, folders, scoping, and the future
   element claim single-homed; shared membership would need item-level
   multi-tenancy on tasks and files and would break the "element view is a
   filter over one project's data" invariant. Cross-project visibility, if
   ever wanted, should be a read-only "linked element" pointer, a separate
   small design.
3. **Default element for existing single-purpose projects?** Recommendation:
   none. A project with zero elements behaves exactly as today, which is the
   cheapest and safest back-compat story; auto-minting a "Main" element in
   every existing project would add a navigation layer nobody asked for and
   make the zero-element fast path dead code. The overview grid appears only
   when the user adds the first element.
4. **Should the group action be offered proactively?** The Projects list
   could suggest grouping when it detects sibling projects with a shared name
   prefix (taOS, taOSmd, taOS Website). Recommendation: yes, but as a quiet,
   dismissible suggestion card in the list pane, never a modal; the action
   itself stays fully manual.
5. **Element slug rename.** Excluded from v1 PATCH because it implies a files
   folder move with all the partial-failure texture of the group action.
   Recommendation: keep excluded in v1; name renames (display) are free, slug
   is fixed at creation like project slugs effectively are today, and a
   rename-with-folder-move ships later as a small follow-up to slice 6's
   folder machinery.
