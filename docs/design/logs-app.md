# Logs app + Providers state model (design, v1)

Closes out the two remaining halves of #1548: a fresh install where every
provider shows "error" and a Settings log viewer that shows nothing, leaving
users unable to self-diagnose. Principle: end users never need a terminal.

## Part 1: Logs app (new platform app)

A dedicated app (dock/launchpad, category `platform`) at the Store/Images
design bar. The existing Settings viewer stays as-is for browser-side client
errors; the Logs app links to it as one source among several.

### Sources (v1)

| Source | Backing | Transport |
| --- | --- | --- |
| Controller | journald unit (system install) or the in-process ring buffer (user-mode/dev) | REST page + SSE tail |
| rkllama | journalctl -u rkllama | REST page + SSE tail |
| qmd | journalctl -u qmd | REST page + SSE tail |
| LLM proxy | its file log under the data dir | REST page + SSE tail |
| Client errors | existing client_log_store | existing endpoint, rendered as a tab |
| Agent containers (v2) | incus/docker logs per agent | deferred; needs per-agent scoping design |

### Backend

New route module `tinyagentos/routes/system_logs.py`:

- `GET /api/system-logs/sources`: the sources available on THIS install
  (journald probed once; absent units simply not listed).
- `GET /api/system-logs/{source}?lines=N&before=cursor`: paged read,
  newest-first, cursor = journald cursor or byte offset.
- `GET /api/system-logs/{source}/stream`: SSE live tail.
- `GET /api/system-logs/bundle`: the copy-for-bug-report payload: the
  installer-style environment banner + last N lines of every source, as one
  redacted text blob.

Rules:
- Session-auth only (operator surface), same middleware as the rest of the API.
- journalctl runs via asyncio subprocess with `--no-pager -o json`, hard line
  caps, and a per-source concurrency guard of one tail per client.
- REDACTION is mandatory and applied server-side before anything leaves the
  box: mask values for key/token/secret/password/authorization patterns,
  bearer headers, and anything matching the secrets store's known names.
  Redaction is a pure function with its own tests; logs never bypass it.

### Frontend

`desktop/src/apps/LogsApp.tsx` + `logs/` per the studio layout conventions:
left rail = sources with live badges, main pane = virtualised log list with
level colouring, follow-tail toggle, search-in-buffer, and a top bar with
"Copy bug report" (calls /bundle, copies to clipboard, toasts). Client-errors
tab reuses the existing feed. Mobile: single-column, source picker as a sheet.

## Part 2: Providers state model

Today `/api/providers` flattens every probe outcome to `status: "error"`,
so a fresh box or a slow-starting service looks broken (#1548 screenshot:
every row red).

Replace the boolean-ish status with an explicit state enum carried per entry:

- `ok`: probe answered.
- `starting`: the backend's lifecycle state says installed/managed but the
  socket is not answering yet (connection refused within the grace window,
  default 120s after controller boot or lifecycle start).
- `unreachable`: probe failed after the grace window; carry `detail` with
  the exception class + target so the UI can show WHY.
- `unconfigured`: cloud type present but no key resolvable.

Frontend: empty list renders a first-run card ("No providers yet") with an
Add Provider CTA instead of an error wall; `starting` renders an amber badge
with auto-refresh; `unreachable` renders red with the detail line and a
Retry probe button. No more undifferentiated red.

## Build order

1. Redaction function + tests (the security core, nothing ships without it).
2. system_logs.py routes + tests (fake journalctl via injected runner).
3. Providers state enum in providers.py + tests + ModelBrowser/Providers UI states.
4. LogsApp frontend + tests.
5. Live verify on the Pi via the control API with screenshots, both surfaces.
