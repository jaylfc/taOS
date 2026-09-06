"""In-memory per-channel typing / thinking heartbeat tracker.

Humans refresh via keystroke-debounced POSTs; agent bridges fire
start/end around their LLM call. Stale entries auto-clear after a
per-kind TTL. Single-process, matches the rest of the chat infra.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal


def _now() -> float:
    return time.monotonic()


Kind = Literal["human", "agent"]
TypingPhase = Literal["thinking", "tool", "reading", "writing", "searching", "planning"]


@dataclass
class _Entry:
    kind: Kind
    expires_at: float
    phase: TypingPhase | None
    detail: str | None


class TypingRegistry:
    def __init__(self, human_ttl: int = 3, agent_ttl: int = 45) -> None:
        self._ttls: dict[str, int] = {"human": human_ttl, "agent": agent_ttl}
        self._entries: dict[tuple[str, str], _Entry] = {}

    def mark(
        self,
        channel_id: str,
        slug: str,
        kind: Kind,
        *,
        phase: TypingPhase | None = None,
        detail: str | None = None,
    ) -> None:
        now = _now()
        ttl = self._ttls[kind]
        resolved_phase: TypingPhase | None = (
            phase if phase is not None else ("thinking" if kind == "agent" else None)
        )
        self._entries[(channel_id, slug)] = _Entry(
            kind=kind,
            expires_at=now + ttl,
            phase=resolved_phase,
            detail=detail,
        )

    def clear(self, channel_id: str, slug: str) -> None:
        self._entries.pop((channel_id, slug), None)

    def list(self, channel_id: str) -> dict[str, list[dict]]:
        now = _now()
        out: dict[str, list[dict]] = {"human": [], "agent": []}
        stale: list[tuple[str, str]] = []
        for (ch, slug), entry in self._entries.items():
            if ch != channel_id:
                continue
            if entry.expires_at < now:
                stale.append((ch, slug))
                continue
            out[entry.kind].append({
                "slug": slug,
                "phase": entry.phase,
                "detail": entry.detail,
            })
        for k in stale:
            self._entries.pop(k, None)
        out["human"].sort(key=lambda e: e["slug"])
        out["agent"].sort(key=lambda e: e["slug"])
        return out
