# Agent loop: subagents for heavy work + safe-point message queue

## Context

The main taOS chat agent (driven over opencode or ACP) processes one user turn
at a time. A turn is **atomic**: it spans prompt, tool calls, and the agent's
full reply. Interrupting a turn mid-tool-call corrupts state (partial edits,
half-written files, dangling subprocesses).

Meanwhile, some user requests kick off heavy, long-running work (codebase
refactors, large file operations, multi-step builds). Blocking the main loop on
that work means the user cannot interrupt or redirect until it finishes, and
the agent cannot surface intermediate results.

## Design

The `AgentLoop` class (in `tinyagentos/agent_loop.py`) wraps the main agent's
turn loop with two mechanisms:

### 1. Subagent delegation

`spawn_subagent(task, worker)` runs an async `worker(progress_cb)` as a
supervised background task. The main loop stays responsive -- it can accept
messages, stream subagent progress via the `sink` callback, and surface
results -- while the subagent works concurrently.

Subagents are tracked in `self._subagents` keyed by id. Each handle records
state (`running` / `completed` / `cancelled` / `failed`), result, and error.

### 2. Safe-point message queue

`handle_message(content, is_redirect=False)` is the single entry point for
incoming user messages:

- **IDLE** -> the message starts a new turn immediately; the loop transitions
  to `WORKING`. Returns `LoopAction.IMMEDIATE`.
- **WORKING** -> the message is appended to the queue; it is **never dropped**
  and **never applied mid-step**. Returns `LoopAction.QUEUED`.

`reach_safe_point()` is called at the end of a turn (the atomic boundary). It:

1. Transitions `WORKING` -> `SAFE_POINT`.
2. Drains the message queue and returns the messages for the caller to act on.
3. If any queued message is a redirect, calls `cancel_subagents()` so in-flight
   subagent work is aborted **at the safe boundary**, never mid-step.
4. Transitions back to `IDLE`.

### 3. Cancel / redirect propagation

`cancel_subagents(reason=None)` cancels every running subagent task and awaits
their unwind under a bounded timeout (mirrors `task_utils.cancel_and_wait`).
A redirect message -- queued during `WORKING` -- triggers this automatically
when `reach_safe_point` processes it.

### 4. Visibility

`status()` returns a snapshot dict with the current loop state, the current
turn id, running subagents (id / task / state / result / error), and the
queued message backlog (id / content / received_at / is_redirect). This lets
the UI show "agent is working" plus "N messages queued".

## State machine

```
IDLE
  |-- handle_message -> WORKING
  |                     |-- spawn_subagent (concurrent)
  |                     |-- handle_message -> QUEUED (buffered)
  |                     |-- reach_safe_point -> SAFE_POINT
  |                            |-- redirect? -> cancel_subagents
  |                            |-- drain queue -> IDLE
SAFE_POINT --(immediate)--> IDLE
```

## Integration points

- The taOS chat endpoint (`routes/taos_agent.py`) can use `AgentLoop` to wrap
  `opencode_runtime.drive_turn`: start a turn, spawn a subagent for long tool
  calls, and drain the queue at the end of the stream.
- The `AgentChatRouter._run_acp_turn` path can delegate to a subagent via
  `openclaw_acp_runtime.drive_turn` when a turn is expected to be long.

## Testing

`tests/test_agent_loop.py` covers:

- **queue-not-drop**: a mid-task message is queued and returned at the safe
  point (never lost).
- **safe-point-delivery**: a queued message is not acted on while the loop is
  `WORKING`; it is surfaced only at `reach_safe_point`.
- **cancel-propagation**: a redirect at the safe point cancels an in-flight
  subagent; `cancel_subagents` directly cancels running tasks.
- **visibility**: `status()` reports running subagents and the queue backlog.
- **subagent lifecycle**: completion, progress streaming, failure, and
  completion-with-running-subagent.
