# Document review surface: reader, review stamps, doc chat (founding design)

Status: PROPOSED (founding design; plan only, no implementation).
Author: jaylfc, 2026-07-12.
Builds on: the decided lead/tool org model
(`docs/design/lead-agent-identity-and-canvas-access.md`, branch
`docs/lead-agent-identity-design` at `4bbbc85e`), the deep-link work from
board task #61 (notification clicks route to apps), and the admin
notification-create endpoint proposed in board task `tsk-ei47at`.

All code citations below were verified against `dev` at `9d006f47`.

## Why this exists

Agents write documents. Design docs, audit reports, migration plans and
research notes all land as markdown in a project's Files folder, and the loop
that gets them REVIEWED is held together by hand today: the authoring agent
inserts a notification row into SQLite manually (there is no create endpoint,
see below), the notification click goes nowhere useful, and Jay ends up
reading raw markdown in a browser download tab, then switching to a chat
channel and describing which document he means before he can say anything
about it.

The vision, in Jay's words (2026-07-12): pressing a review notification
should open the document in a pretty markdown viewer. Docs can be stamped or
marked when an agent owns them or is waiting for a response. The reader has a
pop-out chat pane so Jay can ask the owning agent about the doc, request or
suggest changes, or just say it looks good, straight from the document.

That is the whole product: notification, reader, stamp, conversation, one
surface. This design specifies the four parts and slices them for
implementation.

## Current state, verified in code

### Project files: a plain directory with list/upload/get/trash

- Files live on disk at `data_dir/projects/{slug}/files`:
  `_get_project_files_root` resolves `request.app.state.projects_root / slug
  / "files"` (`tinyagentos/routes/project_files.py:25-32`), and
  `projects_root` is `data_dir / "projects"` (`tinyagentos/app.py:399`).
- `GET /api/projects/{slug}/files` lists entries with name, path, is_dir,
  size, modified (`tinyagentos/routes/project_files.py:87-97`, shared
  `_list_dir` at `:57-79`). An SSE watch stream diffs a directory signature
  (`:100-135`). Upload writes bytes (`:138-160`). `GET
  /api/projects/{slug}/files/{file_path:path}` streams a `FileResponse`
  (`:184-195`). Delete moves to a per-project trash (`:198-219`), restore
  puts it back at the original path (`:231-246`).
- There is NO rename/move endpoint for project files. The Files app checks
  `workspaceRenameUrl`, which returns a URL only for the user workspace and
  `null` for `project:` locations (`desktop/src/apps/FilesApp.tsx:255-259`),
  so Rename/Cut are disabled there. This matters for stamp keying (edge
  cases below).
- No metadata of any kind is attached to a file: no owner, no state, nothing
  but the stat() fields.

### Notifications: a data column, SSE, web push, and no create endpoint

- `NotificationStore.add(title, message, level, source, data)` persists a
  row with a JSON `data` column (`tinyagentos/notifications.py:123-137`,
  schema at `:13-24`, guarded ALTER backfill at `:117-121`), then fans out
  best-effort: an attached SSE event emitter (`:75-83`, invoked at
  `:155-159`) and a PWA web-push sender dispatched as a background task
  (`:85-94`, `:163-171`). Structured payloads already ride `data` today
  (auth-request consent cards).
- Mute preferences exist per event type against a closed `EVENT_TYPES` list
  (`tinyagentos/notifications.py:58-62`).
- The HTTP surface is read/act only: list, archived, read, archive,
  read-all, prefs, push subscription (`tinyagentos/routes/notifications.py`,
  routes at `:28-125`). There is NO endpoint that CREATES a notification.
  This is why the current workflow is a manual sqlite INSERT on the box.
  Board task `tsk-ei47at` adds an admin notification-create endpoint; this
  design goes one step further for reviews and fires the notification
  server-side inside the review-state transition (D3), so the common case
  needs neither a manual insert nor a generic create call.

### Notification clicks today: what #61 built

- The frontend maps each backend row into the client shape in `mapRow`
  (`desktop/src/lib/server-notifications.ts:69-86`), deriving `action` (an
  app id) and `meta` (launch props) from the row's `source` via a hardcoded
  switch, `sourceToTarget` (`:38-67`). Examples: `disk_quota` opens Settings
  at the storage section, `auth_requests` and `decisions` open the Decisions
  app. The row's JSON `data` is passed through onto the notification
  (`:84`).
- Clicking an actionable item opens the target app with `meta` as launch
  props and marks it read: `handleItemClick` calls `openWindow(n.action,
  size, n.meta)` (`desktop/src/components/NotificationCentre.tsx:190-200`).
  The only payload-aware special case is `source === "auth_requests"`, which
  renders inline consent actions from `n.data`
  (`desktop/src/components/NotificationCentre.tsx:42`, `:74-83`).
- So #61's mechanism is source-keyed and app-granular: it can open the
  Projects APP, but it cannot say "this project, Files tab, this file". The
  `meta` prop channel (`Notification.meta`,
  `desktop/src/stores/notification-store.ts:14`) and the `data` passthrough
  are exactly the hooks D1 extends.

### The Projects Files tab: no viewer of any kind

- The Files tab embeds the generic Files app pinned to the project root:
  `<FilesApp rootPath={"project:" + project.slug} />`
  (`desktop/src/apps/ProjectsApp/ProjectWorkspace.tsx:207-213`), which
  resolves URLs through the `project:` prefix branch of `fileUrl`
  (`desktop/src/apps/FilesApp.tsx:167-179`).
- Opening any non-directory file, by double-click, context-menu Open, or the
  list row, is `window.open(fileUrl(...), "_blank")`
  (`desktop/src/apps/FilesApp.tsx:1570`, `:1691-1696`, download at
  `:1717`). A markdown file therefore renders as a raw text download in a
  browser tab. Confirmed: there is no markdown viewer component anywhere in
  the desktop tree; `react-markdown` is imported only by the chat surfaces
  (`desktop/src/apps/MessagesApp.tsx`, `desktop/src/apps/chat/HelpPanel.tsx`
  and a chat render-helpers test).
- Multi-window support exists and Projects already uses it: `openWindow`
  takes `forceNew` and per-window `props`, and re-launching an existing
  window merges props and bumps `launchNonce`
  (`desktop/src/stores/process-store.ts:31`, `:88-104`). The Projects app
  accepts a per-window `projectId` prop and opens sibling windows with
  `forceNew: true` (`desktop/src/apps/ProjectsApp/index.tsx:13-21`).

### Renderer inventory: everything needed is already installed

- `react-markdown` `^10.1.0` and `remark-gfm` `^4.0.1` are existing
  dependencies (`desktop/package.json:65`, `:70`), already used to render
  chat messages (`desktop/src/apps/MessagesApp.tsx:91-92`, `:269-316`).
- Mermaid is already in the dependency tree: `@excalidraw/mermaid-to-excalidraw`
  `^2.2.2` (`desktop/package.json:21`) pulls `mermaid` `^11.12.1`
  (`desktop/package-lock.json:1691`), consumed today by the canvas
  (`desktop/src/apps/ProjectsApp/canvas/mermaid-to-elements.ts`).
- Net new bundle cost of D2 is therefore approximately zero for the core
  path and a lazy chunk for mermaid.

### Ownership and identity: the decided model this design points at

The lead-agent design (`docs/design/lead-agent-identity-and-canvas-access.md`
at `4bbbc85e`) fixed the org model this surface must reference rather than
reinvent:

- Every lead and tool is a registry-minted agent identity with a canonical
  id `{slug}-{YYYYMMDD}-{HHMMSS}` minted once at approve
  (`tinyagentos/agent_registry_store.py:301-305` per that doc). Jay is the
  only human account.
- Lead is an EXCLUSIVE per-project designation that grants nothing by
  itself; capability is orthogonal and per-agent (the canvas read/write
  checkboxes). Events are recorded with uniform actor fields
  (`actor_kind`, `actor_id`).
- Doc ownership in THIS design uses the same vocabulary: an owner is either
  a `canonical_id` (agent) or the user, and every stamp mutation records the
  same uniform actor pair. No new identity concept is introduced.

### How a chat message reaches an owning agent

- Internal path (primary): posting to `POST /api/chat/messages`
  (`tinyagentos/routes/chat.py:265`) persists, broadcasts to the hub, and
  hands the message to `AgentChatRouter.dispatch`
  (`tinyagentos/routes/chat.py:388-391`). Routing rules
  (`tinyagentos/agent_chat_router.py:20-30`): DM channels always route to
  all non-author members with `force_respond=True`; group channels in
  `quiet` mode route only to @mentioned agents; leads are always looped in.
  Delivery drives an OpenClaw ACP turn or a bridge session per message,
  serialized per agent by a lock (`tinyagentos/agent_chat_router.py:54-65`,
  `:66-77`).
- Per-project precedent: every active project has exactly one `a2a` channel,
  enforced by `ensure_a2a_channel`, which creates the channel with
  `settings={"kind": "a2a", ...}` and syncs members
  (`tinyagentos/projects/a2a.py:134-203`). The "one channel with a settings
  kind, one ensure helper, idempotent, per-project lock" shape is exactly
  what the per-doc channel copies.
- External path (secondary): the taOSmd coordination bus is a separate
  service proxied read-only plus one authenticated write,
  `POST /api/a2a/bus/send`, where an agent's `from` is derived from its own
  registry handle and an admin may post as any handle
  (`tinyagentos/routes/a2a_bus.py:147-175`, `:178-218`). Leads whose runtime
  lives outside the controller coordinate there today.

## Design

### D1. Deep-link notifications: a typed target registry

Notification `data` gains an optional typed `target`:

```json
{
  "target": {
    "kind": "project_file",
    "project_id": "prj-...",
    "path": "docs/plan.md"
  }
}
```

- **Backend**: nothing structural. `NotificationStore.add` already persists
  arbitrary JSON `data` (`tinyagentos/notifications.py:123-137`) and both
  the SSE emitter and web push carry it. Producers (D3's transition hook,
  and the `tsk-ei47at` admin create endpoint once it lands) simply include
  `target` in `data`.
- **Frontend**: `mapRow` gains a `targetToAction(data.target)` step that
  takes precedence over the source switch, backed by a REGISTRY keyed by
  `kind` rather than another hardcoded switch
  (`desktop/src/lib/server-notifications.ts:38-67` stays as the fallback
  for source-only rows):

```ts
const TARGET_ROUTES: Record<string, (t: Target) => { action: string; meta: Record<string, string> }> = {
  project_file: (t) => ({
    action: "projects",
    meta: { projectId: t.project_id, tab: "files", filePath: t.path },
  }),
};
```

  Unknown kinds fall through to `sourceToTarget`, so future kinds
  (`project_task`, `canvas_element`, `store_app`, ...) are purely additive:
  one registry entry each, no changes to the click handler.
- **Click routing**: `handleItemClick` already does the right thing with an
  `action` + `meta` pair (`desktop/src/components/NotificationCentre.tsx:190-200`),
  and `meta` is already `Record<string, string>`
  (`desktop/src/stores/notification-store.ts:14`). No changes there.
- **Landing**: the Projects app accepts the new launch props. `projectId`
  exists (`desktop/src/apps/ProjectsApp/index.tsx:13`); `tab` and `filePath`
  are threaded to `ProjectWorkspace`, which owns the tab state
  (`desktop/src/apps/ProjectsApp/ProjectWorkspace.tsx:45`) and passes
  `filePath` into the Files tab to open the viewer (D2). Because
  `openWindow` on an existing window merges props and bumps `launchNonce`
  (`desktop/src/stores/process-store.ts:96-104`), an already-open Projects
  window navigates in place; the components must react to prop changes
  keyed on the nonce, not only on mount.

Result: pressing the review notification opens Projects, selects the
project, switches to Files, and opens the document in the reader. One click.

### D2. Markdown reader: the document viewer

A `DocViewer` component owned by the Projects app, shown when a markdown
file (`.md`, `.markdown`) is opened from a `project:` location. Non-markdown
files keep today's behavior (`window.open` streaming,
`desktop/src/apps/FilesApp.tsx:1691-1696`) untouched.

- **Renderer**: `react-markdown` + `remark-gfm`, the pair already shipped
  and battle-tested in chat (`desktop/package.json:65`, `:70`;
  `desktop/src/apps/MessagesApp.tsx:269-316`). Zero new dependencies for the
  core path; the doc viewer and the chat renderer share one components map
  so code blocks, links and tables look identical across the OS.
- **Sanitization**: `react-markdown` does not render raw HTML by default;
  embedded HTML in a doc appears as literal text. This design deliberately
  does NOT add `rehype-raw`. The default URL transform already neutralizes
  `javascript:` URLs; links additionally render with
  `target="_blank" rel="noopener noreferrer"`. Agent-authored files are
  untrusted input and stay that way.
- **Typography to the taOS bar**: a `.docViewer` prose scope styled with the
  shell tokens (text hierarchy, accent, surface borders) in a CSS module
  next to the component, theme-aware for free since tokens are. Measure
  capped around 72ch, generous heading rhythm, styled blockquotes, zebra
  tables, code blocks with the existing chat treatment.
- **Outline sidebar**: headings h1-h6 register themselves via the
  components map into an outline list (slugified ids, scroll-to on click,
  active section highlighted via IntersectionObserver). No extra remark
  plugin needed. Collapsible; hidden on mobile behind a button.
- **Mermaid**: cheap here because the library is already in the tree
  (`desktop/package.json:21`, `desktop/package-lock.json:1691`). Fenced
  ` ```mermaid ` blocks render through a `React.lazy` wrapper so mermaid
  stays out of the initial chunk; render failure falls back to the plain
  code block. Strict security level, no external resource loading.
- **Windowing**: the viewer opens in place inside the Files tab (breadcrumb
  back to the list), with an "Open in new window" affordance that reuses the
  existing multi-window path: `openWindow("projects", size, { projectId,
  tab: "files", filePath }, { forceNew: true })`, the same pattern the
  project list uses today (`desktop/src/apps/ProjectsApp/index.tsx:17-21`).
  No new app id, no new registry entry.
- **Header bar**: filename, stamp badge (D3), owner chip, Approve / Request
  changes actions (D3), chat pane toggle (D4), download raw, open in new
  window.

### D3. Doc review metadata: stamps

A sidecar store. The markdown file is NEVER mutated: no frontmatter
injection, no stamp comments. A doc's bytes are the author's; its review
state is the system's.

- **Storage: a SQLite table in the existing projects database**, not a JSON
  sidecar file. New `DocReviewStore(BaseStore)` on `data_dir/projects.db`,
  joining `ProjectStore`, `ProjectTaskStore` and the canvas store that
  already share it (`tinyagentos/app.py:372-388`). Justification vs JSON
  sidecars in `files/`:
  - Sidecar files would appear in `_list_dir` and the SSE watch signature
    (`tinyagentos/routes/project_files.py:57-79`), polluting the Files UI
    and every agent's directory scan, or would need fragile hide-listing.
  - Sidecars are user-deletable and trash-restorable as ordinary files,
    so review state could be silently destroyed or forked.
  - Concurrent writers (owner agent flips state while the reviewer acts)
    race on file writes; SQLite gives atomic transitions and an easy
    optimistic-concurrency check.
  - One query answers "everything awaiting my review across projects";
    with sidecars that is a filesystem crawl.
- **Schema** (per file, keyed by project + relative path):

```sql
CREATE TABLE IF NOT EXISTS doc_review (
    project_id      TEXT NOT NULL,
    path            TEXT NOT NULL,
    owner_kind      TEXT NOT NULL,             -- 'agent' | 'user'
    owner_id        TEXT NOT NULL,             -- canonical_id or user id
    review_state    TEXT NOT NULL DEFAULT 'draft'
        CHECK (review_state IN ('draft','awaiting_review','changes_requested','approved')),
    waiting_on      TEXT,                      -- 'user' | canonical_id | NULL
    source_pr       TEXT,                      -- e.g. 'jaylfc/tinyagentos#1797'
    channel_id      TEXT,                      -- per-doc chat channel (D4)
    updated_at      INTEGER NOT NULL,
    updated_by_kind TEXT NOT NULL,             -- uniform actor pair, matching
    updated_by_id   TEXT NOT NULL,             -- the lead-identity design
    PRIMARY KEY (project_id, path)
);
```

  `owner_id` for agents is the registry canonical id
  (`{slug}-{YYYYMMDD}-{HHMMSS}`), exactly the identity the lead design mints
  at approve; the actor columns mirror its uniform actor-event rule. When
  the exclusive Lead designation lands, the default owner for agent-authored
  docs is the project's lead, but ownership stays per-doc and reassignable.
- **State machine**: `draft -> awaiting_review` (owner submits;
  `waiting_on='user'`), `awaiting_review -> approved` or
  `awaiting_review -> changes_requested` (reviewer acts;
  `waiting_on=owner_id` on changes), `changes_requested -> awaiting_review`
  (owner resubmits). Transitions outside these edges are rejected 409.
- **Routes** (`tinyagentos/routes/project_doc_review.py`):
  - `GET /api/projects/{slug}/doc-review`: map of path to stamp for the
    project, one call per Files listing.
  - `PUT /api/projects/{slug}/doc-review/{path:path}`: create/update owner,
    state, `source_pr`; body carries `expected_updated_at` for optimistic
    concurrency (409 on mismatch).
  - Session callers follow the owner/admin gate; agent callers present a
    project-bound registry JWT through the same anchored-allowlist pattern
    the task routes use and the lead design extends to canvas (a
    `doc_review` scope added to the closed vocabulary in both defining
    places, per that design's D1/D2). Agents may set only stamps they own,
    and may not self-approve: `approved` and `changes_requested` are
    reviewer (session) transitions.
- **The automatic review notification**: the transition to
  `awaiting_review` calls `notification_store.add(...)` server-side with
  `source="doc_review"`, a human title ("@taOS-dev requests review:
  plan.md"), and `data.target = {kind: "project_file", project_id, path}`
  (D1). Because `add()` already fans out to SSE and web push
  (`tinyagentos/notifications.py:144-171`), the reviewer's phone buzzes with
  a press-to-open deep link. This is the piece that replaces the manual
  sqlite INSERT that board task `tsk-ei47at`'s admin create endpoint was
  filed to eliminate: the endpoint remains useful for ad-hoc agent
  notifications, but review requests no longer need it because the store
  fires the notification at the moment the state changes. New
  `EVENT_TYPES` entries `doc.review_requested`, `doc.approved`,
  `doc.changes_requested` join the mute-prefs list
  (`tinyagentos/notifications.py:58-62`).
- **Files list badges**: the Files tab fetches the stamp map alongside the
  listing and renders a compact badge per stamped file: amber "awaiting
  review", red "changes requested", green "approved", neutral "draft", plus
  an owner initial chip. Unstamped files render exactly as today.
- **Reader actions**: Approve and Request changes buttons in the DocViewer
  header drive the PUT. Request changes requires text, which becomes the
  first message in the doc chat (D4); Approve posts a structured
  looks-good message there too, so the conversation and the state always
  agree.

### D4. Review chat pane: talk to the owner from the document

A pop-out pane bound to `(project, file)`, toggled from the reader header:
slide-over on desktop, bottom sheet on mobile.

- **Channel**: one internal message-hub channel per reviewed doc, named
  `doc-{project_slug}-{file-stem}` (e.g. `doc-taos-plan`), created lazily by
  an idempotent `ensure_doc_channel(...)` modeled on `ensure_a2a_channel`
  (`tinyagentos/projects/a2a.py:134-203`): identified by
  `settings.kind="doc_review"` plus `settings.path`, `project_id` set,
  members = the doc owner's agent name + the user, and its id persisted in
  `doc_review.channel_id`. Collisions on the human-readable name are
  disambiguated by the settings identity, exactly like the a2a invariant.
- **Type: DM.** DM channels route every message to all non-author members
  with `force_respond=True` (`tinyagentos/agent_chat_router.py:24`), so a
  question typed in the pane reaches the owner without @mention ceremony,
  unlike the project a2a group channel in quiet mode (`:25`). The pane is a
  direct line to the owner ABOUT THIS DOC, and DM semantics encode that.
- **Message path**: the pane posts through the ordinary
  `POST /api/chat/messages` (`tinyagentos/routes/chat.py:265`); persistence,
  hub broadcast and router dispatch (`tinyagentos/routes/chat.py:388-391`)
  come for free, as does the message renderer shared with D2.
- **Quick actions**: three affordances above the composer:
  - **Approve**: runs the D3 transition to `approved`, then posts a
    structured message (metadata `{kind: "review_action", action:
    "approved"}`) rendered as a green system-style card.
  - **Request changes**: free-text required; transition to
    `changes_requested`, text posted with metadata `{kind: "review_action",
    action: "changes_requested"}`.
  - Plain questions and suggestions: ordinary messages, no state change.
- **Counterparty notifications**: `awaiting_review` notifies the user (D3).
  `approved` / `changes_requested` notify the owner: for an internal-hub
  agent the routed DM message IS the wake-up (the router drives a turn per
  message); the stamp change and structured message give it machine-readable
  state. For a lead whose runtime lives on the external taOSmd bus, a mirror
  post to a bus thread of the same name via the authenticated send proxy
  (`tinyagentos/routes/a2a_bus.py:178-218`) is the bridge; that mirroring is
  roadmap, not v1 (open question 1 notes the tradeoff).
- **Honest latency note**: this pane is not a live chat with a human.
  Replies arrive when the owner agent's runtime processes the turn: dispatch
  is fire-and-forget into a per-agent serialized lock
  (`tinyagentos/agent_chat_router.py:54-77`), so an agent that is mid-task,
  cold, or offline replies late or not until its next wake. The pane must
  say so: a quiet "delivered, waiting for {owner}" status line after send,
  no fake typing indicators, and the notification path (not the pane)
  carries the eventual reply to the user if the pane is closed.
- **Decisions app tie-in (#65)**: an `awaiting_review` stamp is a
  decision-shaped item. V1 keeps `source="doc_review"` with its own
  deep-link target so nothing blocks on the Decisions app; when Decisions
  matures, it reads the same `doc_review` table to list pending reviews and
  reuses the same PUT transitions, and `sourceToTarget`'s decisions routing
  (`desktop/src/lib/server-notifications.ts:61-63`) can adopt the source.
  No schema change anticipated.

## Slice plan

Each slice is one PR against `dev`, independently shippable in order.
Slices 1-4 are bounded, spec-complete work suitable for an external CLI
coding agent. Slice 5 is UI-only and also boundable. Slice 6 touches router
and channel invariants and is flagged maintainer-review.

1. **Markdown reader** (external-agent slice)
   - `desktop/src/apps/ProjectsApp/files/DocViewer.tsx` (+ `DocViewer.module.css`,
     `OutlinePane.tsx`, `MermaidBlock.tsx` lazy wrapper)
   - `desktop/src/apps/FilesApp.tsx`: `openFile` branches to the viewer for
     `.md`/`.markdown` under `project:` locations only (today's
     `window.open` at `:1691-1696` stays for everything else)
   - `desktop/src/apps/ProjectsApp/__tests__/DocViewer.test.tsx`
   - Verify: `cd desktop && npx vitest run src/apps/ProjectsApp` and
     `npm run build` (bundle check: no mermaid in the entry chunk); manual:
     Projects -> Files -> open a `.md`, headings/outline/tables/code render,
     non-md unchanged.

2. **Deep-link target registry** (external-agent slice)
   - `desktop/src/lib/server-notifications.ts`: `TARGET_ROUTES` registry +
     `targetToAction` precedence in `mapRow`
   - `desktop/src/apps/ProjectsApp/index.tsx` and
     `desktop/src/apps/ProjectsApp/ProjectWorkspace.tsx`: accept `tab` and
     `filePath` launch props, react to `launchNonce` prop merges
   - `desktop/src/lib/__tests__/server-notifications.test.ts`
   - Verify: `cd desktop && npx vitest run src/lib`; manual: insert a test
     notification row with a `project_file` target, click it, land in the
     viewer.

3. **Stamp store + routes + badges** (external-agent slice)
   - `tinyagentos/projects/doc_review_store.py`,
     `tinyagentos/routes/project_doc_review.py`, wiring in
     `tinyagentos/app.py` beside the other projects stores (`:372-388`)
   - `desktop/src/apps/FilesApp.tsx`: stamp badge column for `project:`
     locations; `desktop/src/lib/projects.ts`: API client
   - `tests/test_doc_review.py` (state machine, actor recording, 409s,
     agent-scope gating)
   - Verify: `uv run pytest tests/test_doc_review.py -q`; manual curl:
     `curl -X PUT .../api/projects/{slug}/doc-review/docs%2Fplan.md -d
     '{"review_state":"awaiting_review", ...}'` then `GET .../doc-review`.

4. **Review notification hook + reader actions** (external-agent slice)
   - `tinyagentos/projects/doc_review_store.py`: transition hook calling
     `notification_store.add` with `data.target`; `EVENT_TYPES` additions in
     `tinyagentos/notifications.py:58-62`
   - `desktop/src/apps/ProjectsApp/files/DocViewer.tsx`: header badge,
     Approve / Request changes actions driving the PUT
   - Extend `tests/test_doc_review.py`: transition emits a notification row
     whose `data.target` round-trips through `mapRow`
   - Verify: `uv run pytest tests/test_doc_review.py -q`; end-to-end: agent
     sets `awaiting_review`, bell rings, click opens the doc, Approve flips
     the badge. This closes the loop of slices 1-4.

5. **Chat pane UI** (external-agent slice, feature-flagged)
   - `desktop/src/apps/ProjectsApp/files/DocChatPane.tsx`: pop-out pane
     bound to `doc_review.channel_id`; renders channel messages via the
     existing chat plumbing, composer posts to `POST /api/chat/messages`;
     quick-action buttons render but call the D3 PUT only (structured
     message posting arrives with slice 6); "waiting for {owner}" status
   - Hidden behind a `doc_review_chat` flag until slice 6 lands (the pane
     shows an empty state when `channel_id` is null)
   - Verify: `cd desktop && npx vitest run src/apps/ProjectsApp`; manual
     against a hand-created channel.

6. **Per-doc channel plumbing** (maintainer-review)
   - `tinyagentos/projects/doc_review_channels.py`: `ensure_doc_channel`
     (idempotent, settings-identified, DM type, per-doc lock), invoked on
     first pane open or first transition; `channel_id` persisted
   - Counterparty notification wiring for `approved` /
     `changes_requested`; structured `review_action` messages posted on the
     D3 transitions; flag removed
   - `tests/test_doc_review_channels.py` (idempotency, duplicate
     reconciliation, membership sync on owner reassignment)
   - Verify: `uv run pytest tests/test_doc_review_channels.py -q`; live:
     question typed in the pane reaches the owner agent (router log shows
     DM force-respond dispatch), reply lands in the pane.
   - Flagged maintainer-review because it adds a channel invariant beside
     `ensure_a2a_channel` and leans on DM routing semantics
     (`tinyagentos/agent_chat_router.py:20-30`); a mistake here spams every
     project agent.

## Edge cases

- **File renamed**: there is no rename endpoint for project files today
  (`desktop/src/apps/FilesApp.tsx:255-259`), so renames happen out-of-band
  (an agent rewriting the tree, or delete + re-upload). A stamp keyed by
  path then points at nothing. The `GET .../doc-review` merge marks stamps
  whose path is absent from disk as `missing: true`; the Files tab surfaces
  them in a small "detached reviews" row with reattach (pick the new path,
  key is updated in one transaction) and discard actions. If a project
  rename endpoint lands later, it must update `doc_review.path` in the same
  request.
- **File deleted**: delete is move-to-trash
  (`tinyagentos/routes/project_files.py:198-219`); the stamp row is KEPT
  (audit trail plus the restore path at `:231-246` returns the file to the
  same path, reattaching naturally). Purging from trash marks the stamp
  `missing`; the doc channel is archived, never deleted.
- **Markdown with embedded HTML**: rendered as literal text (no
  `rehype-raw`, D2); `javascript:` and `data:` URLs neutralized by the
  default URL transform; images load only from same-origin file URLs.
- **Very large docs**: above a threshold (256 KB soft, 1 MB hard) the viewer
  shows the header, stamp and chat pane but replaces the body with a
  "too large to render inline" card offering raw open and download; the
  outline is skipped. No windowed rendering in v1.
- **Concurrent transitions**: `expected_updated_at` optimistic concurrency
  on the PUT; a 409 makes the loser refetch and re-render the real state.
- **Mobile**: the viewer is a full-screen page under the Files pill
  (`desktop/src/apps/ProjectsApp/ProjectWorkspace.tsx:63`), outline behind a
  toolbar button, Approve / Request changes in a sticky footer, chat pane as
  a bottom sheet. Web push already reaches the phone
  (`tinyagentos/notifications.py:85-94`), so notify -> press -> read ->
  approve works end-to-end away from the desk.
- **Owner reassignment**: updating `owner_id` syncs the doc channel
  membership (slice 6) and rewrites `waiting_on` if it pointed at the old
  owner.

## Open questions, with recommendations

1. **Per-doc thread vs project channel with a tag.** A single project
   channel tagged per file keeps channel count low but forces the owner
   agent to filter every message and floods the reviewer's pane with
   unrelated traffic; per-doc DM channels give the agent a bounded context
   window, make `force_respond` routing trivial, and archive cleanly with
   the doc. Recommendation: PER-DOC channel (as designed), with the
   `ensure_` helper reconciling duplicates like the a2a invariant does.
   Revisit only if channel-per-doc volume becomes a real listing problem.
2. **Should Approve auto-close the source PR?** `source_pr` is a link, and
   it is tempting to wire `approved` to a `gh pr close`/merge. Recommendation:
   NO. PR lifecycle has its own gates (CI, bot reviews, human merge policy)
   and taOS should not mutate git state as a side effect of a doc stamp. A
   later board automation can opt in explicitly per project.
3. **Inline selection annotations** (highlight a passage, comment on it,
   like a code review). Genuinely wanted, but it needs an anchoring model
   that survives the owner editing the doc between rounds, which is a
   design of its own. Recommendation: ROADMAP after v1; the per-doc channel
   plus quoted-text convention ("> quoted line" prefixes, one composer
   button) covers 80% meanwhile, and the structured `review_action` message
   shape leaves room for an `anchors` field later.
4. **Scope naming for agent stamp writes.** A dedicated `doc_review` scope
   (recommended: it is one closed-vocabulary entry in the two defining
   places, per the lead design's D1) vs overloading `project_tasks`.
   Overloading is less code today but couples two unrelated capabilities
   forever; keep them separate.
