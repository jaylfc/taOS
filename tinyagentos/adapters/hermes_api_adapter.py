"""Hermes API adapter — drives the Hermes OpenAI-compatible API server via
POST /v1/chat/completions and maps its answer onto taOS's reply kinds.

Same interface as :class:`~tinyagentos.adapters.picoclaw_cli_adapter.PicoClawCliAdapter`
so the taOS Agent chat route drives either one unchanged::

    adapter = HermesApiAdapter(HermesApiConfig(url=..., key=...), sink)
    await adapter.ensure_session()
    await adapter.prompt("Hello", trace_id="t1", attachments=[])
    await adapter.close()

Hermes' api_server is OpenAI-compatible: ``POST {url}/v1/chat/completions`` with
header ``Authorization: Bearer <key>`` and body ``{"model": "hermes", "messages": [...]}``,
answer at ``choices[0].message.content``. Do NOT use ``stream: true``: SSE support
has not been measured on our pinned Hermes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HermesApiConfig:
    url: str
    key: str
    system: str | None = None
    history: list[dict] = field(default_factory=list)
    timeout: float = 300.0


class HermesApiAdapter:
    def __init__(self, config: HermesApiConfig, sink) -> None:
        self._cfg = config
        self._sink = sink
        self.session_id: str | None = None

    async def _emit(self, reply: dict) -> None:
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("hermes_api_adapter: sink raised for reply kind=%s", reply.get("kind"))

    async def ensure_session(self) -> str:
        if self.session_id is None:
            self.session_id = "hermes-session"
        return self.session_id

    async def prompt(
        self,
        text: str,
        trace_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        if attachments:
            await self._emit({
                "kind": "error",
                "trace_id": trace_id,
                "error": "attachments are not supported by the Hermes taOS Agent yet; send the message without them",
            })
            return

        try:
            await self.ensure_session()

            messages = []
            if self._cfg.system is not None:
                messages.append({"role": "system", "content": self._cfg.system})
            messages.extend(self._cfg.history)
            messages.append({"role": "user", "content": text})

            headers = {"Authorization": f"Bearer {self._cfg.key}"}
            body = {"model": "hermes", "messages": messages}

            async with httpx.AsyncClient(timeout=self._cfg.timeout) as client:
                resp = await client.post(
                    f"{self._cfg.url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                )

            if resp.status_code != 200:
                await self._emit({
                    "kind": "error",
                    "trace_id": trace_id,
                    "error": f"Hermes returned HTTP {resp.status_code}",
                })
                return

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not content:
                await self._emit({
                    "kind": "error",
                    "trace_id": trace_id,
                    "error": f"Hermes returned HTTP {resp.status_code}: missing content",
                })
                return

            await self._emit({"kind": "delta", "trace_id": trace_id, "content": content})
            await self._emit({"kind": "final", "trace_id": trace_id, "content": content})

        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            await self._emit({
                "kind": "error",
                "trace_id": trace_id,
                "error": f"Hermes request failed: {type(exc).__name__}",
            })
        except Exception as exc:  # noqa: BLE001 - degrade to an error reply
            logger.error("hermes_api_adapter: turn failed: %s", type(exc).__name__)
            await self._emit({
                "kind": "error",
                "trace_id": trace_id,
                "error": f"the taOS agent (hermes) failed: {exc}",
            })

    async def close(self) -> None:
        return None