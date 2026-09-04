# Audit: post-merge review of PRs #2710, #2709, #2704

**Generated:** 2026-09-03
**Task:** tsk-taqcij — Post-merge review of 3 PRs merged without CodeRabbit review
**Method:** Static review of merged diffs (`gh pr diff <n> --repo jaylfc/taOS`) and the
corresponding current state on `origin/dev`. Read-only: **no source behavior was changed**.
Baseline verified green: 62 tests across the projects surface pass
(`tests/test_routes_projects.py`); 5 reaper tests pass (`tests/test_reaper.py`);
4 quarantine task-store tests pass (`tests/test_task_store.py`); desktop build passes;
66 board/component tests pass (`desktop/src/apps/ProjectsApp/board/__tests__/`).

---

## #2710 — ready_tasks view: blocked-on label join + limit clamp

**Files reviewed:** `tinyagentos/projects/task_store.py`, `tinyagentos/routes/projects.py`
**Changes:** `ready_tasks` view gains a `blocked-on:<id>` label subquery scoped to the same
project; `list_ready_tasks` clamps `limit` from `[1, 200]` to `[1, 500]`; the route handler
exposes `?limit` to the caller; `_post_init` drops and recreates the view for migrated DBs.

**Severity: none — no findings.**

The `ready_tasks` view body in `tinyagentos/projects/task_store.py:71` correctly joins
`json_each(t.labels)` to `project_tasks bt` with `bt.project_id = t.project_id`, preventing
cross-project label leakage. `_post_init` (`tinyagentos/projects/task_store.py:217`) uses
`DROP VIEW IF EXISTS` + `CREATE VIEW` to force the new body onto existing databases, which is
the correct approach since `CREATE VIEW IF NOT EXISTS` is a no-op on an existing view. The
clamp at `tinyagentos/projects/task_store.py:666` (`limit = max(1, min(limit, 500))`) correctly
prevents the SQLite `LIMIT -1` unbounded trap. The route at
`tinyagentos/routes/projects.py:920` declares `limit: int = 50` and forwards it; the
`_authorize_task_actor` guard runs before the store call, so authorisation is unchanged.

**What was checked:** SQL injection surface (no user-supplied values interpolated into the view
body or the store query -- `limit` is bound as a parameter); cross-project data leakage (view
join constrained by `bt.project_id = t.project_id`); limit floor/ceiling correctness;
migration safety (DROP+CREATE is atomic within the `_post_init` transaction); route-level
authorisation ordering.

---

## #2709 — reap_hung_executor_sh: third rule for hung lanes

**Files reviewed:** `tinyagentos/scheduling/reaper.py`
**Changes:** New module implementing `reap_hung_executor_sh(cap_seconds)`. Iterates
`psutil.process_iter`, selects processes whose `cmdline` contains `"executor.sh"`, kills those
older than `cap_seconds` whose PPID is not 1, and returns the reaped list.

**Severity: none — no findings.**

The process filter at `tinyagentos/scheduling/reaper.py:19` uses
`any("executor.sh" in str(part) for part in cmdline)`, which correctly matches any argv
element containing the substring (catches `python executor.sh`, `bash -c 'executor.sh ...'`,
etc.) without false positives from unrelated process names. The `ppid()` call at
`tinyagentos/scheduling/reaper.py:26` is wrapped in `except (psutil.NoSuchProcess,
psutil.AccessDenied)`; `psutil.ZombieProcess` is a subclass of `NoSuchProcess` and is
therefore caught. The kill/wait block at `tinyagentos/scheduling/reaper.py:31` handles
`NoSuchProcess` (process exited between age-check and kill) and `TimeoutExpired` (process
unresponsive to SIGKILL within 5 s) without swallowing unexpected errors. The PPID-1 guard at
`tinyagentos/scheduling/reaper.py:29` correctly avoids killing adopted orphans whose real
parent is gone. `proc.info.get("create_time") or 0` at line 21 means a process whose
`create_time` is unavailable is aged as 0 and skipped.

**What was checked:** psutil exception hierarchy (`ZombieProcess` -> `NoSuchProcess`); race
between age-check and kill; PPID-1 guard correctness; cmdline substring match scope; return
shape; absence of privilege escalation (reaper does not open files or network sockets).

---

## #2704 — Board a11y: Unquarantine button keyboard reachability + quarantine column

**Files reviewed:** `desktop/src/apps/ProjectsApp/board/ProjectBoard.tsx`,
`desktop/src/apps/ProjectsApp/board/BoardColumn.tsx`,
`desktop/src/apps/ProjectsApp/board/BoardLane.tsx`,
`desktop/src/apps/ProjectsApp/board/TaskCard.tsx`,
`desktop/src/apps/ProjectsApp/board/TaskCard.module.css`,
`desktop/src/apps/ProjectsApp/board/ProjectBoard.module.css`,
`desktop/src/apps/ProjectsApp/board/BoardColumn.module.css`,
`desktop/src/apps/ProjectsApp/board/BoardLane.module.css`,
`desktop/src/apps/ProjectsApp/board/types.ts`,
`desktop/src/apps/ProjectsApp/board/useBoardData.ts`,
`desktop/src/lib/projects.ts`

### Finding 1 — dispatchDnd silently drops drags onto quarantined cells in lanes view

**Severity:** Medium. **Surface:** drag-and-drop UX in lanes view.

**Reproduction:** Switch board to lanes view, drag any non-quarantined task card over a
quarantined cell, drop. `BoardLane` calls
`onDropTask={(id, status, laneKey) => dispatchDnd(id, status, laneKey)}` unconditionally
(`desktop/src/apps/ProjectsApp/board/ProjectBoard.tsx:251`). `dispatchDnd` has a guard
`if (columnStatus === "quarantined") return;` at line 119 that returns without calling
`dndAction`, without `setAnnouncement`, and without `window.alert`. The card snaps back to its
original position with zero user-visible feedback.

**Code path:**

```text
BoardLane onDropTask -> dispatchDnd(taskId, "quarantined", laneKey)
  -> line 119: if (columnStatus === "quarantined") return;   <- silent exit
```

In kanban view the column passes `onDropTask={s === "quarantined" ? undefined : ...}`
(`desktop/src/apps/ProjectsApp/board/ProjectBoard.tsx:226`), so the browser never fires the
drop event for that column. In lanes view there is no equivalent guard -- `BoardLane` always
passes the handler. The guard at line 119 is therefore reachable in lanes view and produces a
silent no-op.

### Keyboard/ARIA checks (no findings)

- The Unquarantine `<button>` at `desktop/src/apps/ProjectsApp/board/TaskCard.tsx:60` has
  `type="button"`, an `aria-label` (`Unquarantine task {task.id}`), and a `:focus-visible`
  outline in `desktop/src/apps/ProjectsApp/board/TaskCard.module.css:34`.
- Keyboard activation: Enter/Space on the focused button call `e.stopPropagation()` +
  `e.preventDefault()` before `onUnquarantine`, preventing the parent `role="button"` div's
  `onKeyDown` from also firing.
- The quarantine badge `role="status"` at `desktop/src/apps/ProjectsApp/board/TaskCard.tsx:53`
  carries an `aria-label` and is placed inside the card in reading order.
- The Quarantined column header carries `aria-label="Quarantined"` via the `NAME` map in
  `desktop/src/apps/ProjectsApp/board/BoardColumn.tsx:20`.
- The `task.unquarantined` handler in
  `desktop/src/apps/ProjectsApp/board/useBoardData.ts:62` resets `status` to `"open"`,
  clears `claimed_by`, and clears `strike_count`/`latest_strike` -- state returns to a
  non-quarantined baseline correctly.
- The `task.quarantined` SSE handler (`desktop/src/apps/ProjectsApp/board/useBoardData.ts:55`) updates `status` and carries
  `strike_count`/`latest_strike` from the event payload, matching what the backend publishes
  at `tinyagentos/projects/task_store.py:549`.
- The `unquarantine` API method at `desktop/src/lib/projects.ts:275` uses the shared `http`
  function which applies `withCsrf` -- the POST is CSRF-protected and the route at
  `tinyagentos/routes/projects.py:1248` requires `_authorize_project_lead`.

---

## Fix-forward cards cut

| PR | Finding | Card |
| --- | --- | --- |
| #2704 | dispatchDnd silently swallows DnD onto quarantined cells in lanes view (Finding 1, Medium) | #2758 |
| #2710 | No Bug/Security findings -- none cut | -- |
| #2709 | No Bug/Security findings -- none cut | -- |
