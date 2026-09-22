# Reachability finding for tsk-hpee75

## Question
Can the openclaw bridge client emit `delta` or `tool_result` with `trace_id` absent?

## Evidence

### 1. The old fork-based bridge client was never shipped
`docs/design/openclaw-integration.md` states:
> No fork. `jaylfc/openclaw` is archived. `src/taos-bridge.ts` was never shipped.

The npm package `openclaw@2026.9.5` contains no `taos-bridge.ts` or any code that
POSTs to `/api/openclaw/sessions/{agent}/reply`.

### 2. The current ACP adapter always includes `trace_id`
`tinyagentos/adapters/acp_adapter.py` is the live emitter. It maps ACP
`session/update` notifications onto `delta`, `final`, `tool_call`, `tool_result`,
`reasoning`, and `error` reply dicts.

Every emitted reply carries a `trace_id`:
- `prompt()` sets `self._active_trace = trace_id or uuid.uuid4().hex` (line 317)
- `_handle_notification()` resolves `trace = self._active_trace or params.get("sessionId") or uuid.uuid4().hex` (line 352)
- All `_emit()` calls include `"trace_id": trace` (lines 360, 365, 370, 380, 394, 433)

There is no code path in the adapter that emits `delta` or `tool_result` without a
`trace_id`.

### 3. The `openclaw_acp_runtime.py` error path
When the ACP transport fails before or during `adapter.prompt()`, the runtime
emits an `error` reply with `trace_id: trace_id`. If the caller passed `None`,
the adapter would have minted one inside `prompt()` before the failure, or the
error reply falls back to a generated UUID in `bridge_session.py` line 264.
This is an `error` event, not `delta` or `tool_result`, so it does not trigger
the silent no-op described in the card.

## Conclusion
**Unreachable.** The current bridge client (ACP adapter) always includes
`trace_id` on `delta` and `tool_result` events. The old fork-based bridge that
the comment on line 269 refers to was never shipped. No fix is required.
