"""Post-hoc reasoning judge — Phase 4 observability.

Reads a completed trace set and asks a local model to assess whether the
agent's reasoning path supported its conclusions. Stores the verdict as a
``reasoning_audit`` trace event (never emitted as an OTel span — spec §4.7).

Triggered by ``AgentTraceStore.record()`` when it sees
``kind="lifecycle", payload.event="session_end"``.

MVP constraint (spec §4.7): only non-trivial runs (≥1 llm_call AND ≥1 tool_call)
are judged — trivial runs exit early with no event written.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from tinyagentos.trace_store import AgentTraceStore

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = """\
You are a silent reasoning auditor for an AI agent. \
Analyse the trace below and check whether the agent's reasoning correctly led to its conclusions. \
Reply with JSON only — no prose, no markdown fences."""

_JUDGE_USER = """\
Assess the following agent run trace:

{trace_text}

Respond with exactly this JSON shape:
{{"verdict": "pass" | "warn" | "fail", "flags": ["<short issue description, or empty list>"]}}

Verdict guide:
- pass: reasoning is sound; conclusions follow from evidence
- warn: minor issues (one ignored result, slight drift) but overall coherent
- fail: fundamental failure (conclusion contradicts tool results, circular logic, major ignored evidence)

Focus on: (1) did the agent use tool results before drawing conclusions; \
(2) were any tool results ignored or contradicted; \
(3) any internally inconsistent reasoning steps."""


def _format_trace(events: list[dict]) -> str:
    """Render trace events as readable text for the judge prompt."""
    lines: list[str] = []
    for evt in events:
        kind = evt.get("kind", "?")
        payload = evt.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}

        if kind == "llm_call":
            model = evt.get("model") or "?"
            tokens = (evt.get("tokens_in") or 0) + (evt.get("tokens_out") or 0)
            status = payload.get("status", "?")
            lines.append(f"[llm_call] model={model} tokens={tokens} status={status}")
        elif kind == "tool_call":
            tool = payload.get("tool", "?")
            args_raw = json.dumps(payload.get("args", {}), ensure_ascii=False)
            lines.append(f"[tool_call] {tool}({args_raw[:120]})")
        elif kind == "tool_result":
            tool = payload.get("tool", "?")
            ok = payload.get("success", True)
            result_preview = str(payload.get("result", ""))[:200]
            lines.append(f"[tool_result] {tool} success={ok} result={result_preview!r}")
        elif kind == "reasoning":
            text = str(payload.get("text", ""))[:500]
            lines.append(f"[reasoning] {text}")
        elif kind == "message_out":
            content = str(payload.get("content", ""))[:300]
            lines.append(f"[message_out] {content}")
        elif kind == "error":
            lines.append(f"[error] {payload.get('message', '?')}")
        # skip message_in, lifecycle, reasoning_audit (meta-events)

    return "\n".join(lines) if lines else "(empty trace)"


def _resolve_judge_model(override: str | None) -> str:
    """Return the model id to use for judging.

    Priority: explicit override → system-wide memory model → fallback.
    The fallback is a free tier so the judge never hard-fails on installs
    that have no local model configured yet.
    """
    if override:
        return override
    try:
        import taosmd  # type: ignore[import-untyped]
        getter = getattr(taosmd, "get_memory_model", None)
        if getter:
            m = getter()
            if m:
                return m
    except Exception:
        pass
    return "kilo-auto/free"


class ReasoningJudge:
    """One instance per AgentTraceStore. Injected via set_judge()."""

    def __init__(
        self,
        *,
        litellm_base_url: str = "http://127.0.0.1:4000/v1",
        litellm_api_key: str = "taos-internal",
        judge_model: str | None = None,
        timeout: float = 60.0,
    ):
        self._base_url = litellm_base_url.rstrip("/")
        self._api_key = litellm_api_key
        self._judge_model = judge_model
        self._timeout = timeout
        self._pending_tasks: set[asyncio.Task] = set()

    def schedule(self, store: "AgentTraceStore", trace_id: str) -> None:
        """Schedule a judge run as a fire-and-forget background task.

        No-op when there is no running event loop (e.g. during tests that
        call record() synchronously outside of asyncio).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run(store, trace_id))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _run(self, store: "AgentTraceStore", trace_id: str) -> None:
        """Run the judge; all errors are swallowed so the judge is never fatal."""
        try:
            await self._run_inner(store, trace_id)
        except Exception:
            logger.exception("reasoning_judge: non-fatal error for trace %s", trace_id)

    async def _run_inner(self, store: "AgentTraceStore", trace_id: str) -> None:
        events = await store.list(trace_id=trace_id, limit=500)

        llm_count = sum(1 for e in events if e.get("kind") == "llm_call")
        tool_count = sum(1 for e in events if e.get("kind") == "tool_call")
        if llm_count < 1 or tool_count < 1:
            logger.debug(
                "reasoning_judge: skipping trivial trace %s (llm=%d tool=%d)",
                trace_id, llm_count, tool_count,
            )
            return

        trace_text = _format_trace(events)
        model = _resolve_judge_model(self._judge_model)
        t0 = time.time()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _JUDGE_SYSTEM},
                        {"role": "user", "content": _JUDGE_USER.format(trace_text=trace_text)},
                    ],
                    "max_tokens": 256,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()

        latency_ms = int((time.time() - t0) * 1000)
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if the model wrapped the JSON.
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        verdict_data = json.loads(content)
        verdict = verdict_data.get("verdict", "warn")
        flags: list[str] = verdict_data.get("flags") or []

        if verdict not in ("pass", "warn", "fail"):
            flags.append(f"unexpected verdict value {verdict!r}")
            verdict = "warn"

        await store.record(
            "reasoning_audit",
            trace_id=trace_id,
            payload={
                "verdict": verdict,
                "flags": flags[:10],
                "model": model,
                "latency_ms": latency_ms,
            },
        )
        logger.info(
            "reasoning_audit: trace=%s verdict=%s flags=%s model=%s latency=%dms",
            trace_id, verdict, flags, model, latency_ms,
        )
