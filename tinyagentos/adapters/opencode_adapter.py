"""opencode adapter — drives opencode's headless HTTP server and maps its SSE
event stream onto taOS's fixed 6-kind reply vocabulary:
  delta | final | tool_call | tool_result | reasoning | error

opencode server: `opencode serve --port <n> --hostname 127.0.0.1`
Optional HTTP Basic auth when OPENCODE_SERVER_PASSWORD is set.

Usage (callers drive the adapter):
    # sink receives reply dicts (the body shape bridge_session.record_reply
    # consumes): {"kind": ..., "trace_id": ..., "content"/"error": ...}
    cfg = OpenCodeConfig(base_url="http://127.0.0.1:5888", ...)
    adapter = OpenCodeAdapter(cfg, sink=my_sink)
    await adapter.prompt("Hello", trace_id="t1")
    await adapter.close()
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OpenCodeConfig:
    """Configuration for a single opencode server instance."""

    base_url: str
    """Base URL of the opencode server, e.g. http://127.0.0.1:5888"""

    server_password: str | None = None
    """If set, HTTP Basic auth is used (username defaults to 'opencode')."""

    server_username: str = "opencode"
    """HTTP Basic auth username; only used when server_password is set."""

    model_provider_id: str = "litellm"
    """opencode provider ID, e.g. 'litellm'."""

    model_id: str = ""
    """opencode model ID, e.g. 'stepfun/step-3.7-flash:free'."""

    agent: str | None = None
    """Optional agent slug for informational purposes."""

    system: str | None = None
    """Unused by the opencode API directly; reserved for future injection."""

    connect_timeout: float = 10.0
    read_timeout: float = 300.0


# ---------------------------------------------------------------------------
# Pure event mapper (unit-testable, no I/O)
# ---------------------------------------------------------------------------

def map_opencode_event(evt: dict, state: dict) -> list[tuple[str, dict]]:
    """Map one opencode SSE event dict onto zero or more (kind, payload) tuples.

    Args:
        evt:   Parsed JSON dict from a ``data: ...`` SSE line.
        state: Mutable dict shared across a single turn:
               - ``"session_id"``    — the session we own (set before streaming).
               - ``"text"``          — accumulated final-text buffer (str).
               - ``"done"``          — True once session.idle fires.
               - ``"switched_model"``— last model reported by session.next.model.switched.

    Returns:
        List of ``(kind, payload)`` where ``kind`` is one of the 6 reply
        kinds and ``payload`` is kwargs for ``record_reply``. Returns ``[]``
        for events we ignore or that belong to a different session.
    """
    evt_type = evt.get("type", "")
    props = evt.get("properties") or {}
    our_session = state.get("session_id")

    # Helper: does this event belong to our session?
    def _our(sid_key: str = "sessionID") -> bool:
        if our_session is None:
            return True  # before session is established; accept
        return props.get(sid_key) == our_session

    # ------------------------------------------------------------------ ignore
    if evt_type in ("server.connected", "session.created", "message.updated"):
        return []

    # -------------------------------------------- streaming text / reasoning
    if evt_type == "message.part.delta":
        if not _our():
            return []
        field = props.get("field", "")
        delta = props.get("delta", "")
        if field == "text":
            state["text"] = state.get("text", "") + delta
            return [("delta", {"content": delta})]
        if field == "reasoning":
            return [("reasoning", {"content": delta})]
        return []

    # -------------------------------------------- part reached a new state
    if evt_type == "message.part.updated":
        part = props.get("part") or {}
        part_type = part.get("type", "")
        if part_type != "tool":
            return []
        # Only act on parts that belong to our session (best-effort — part
        # objects don't always carry sessionID; we accept if no filter hits).
        part_session = part.get("sessionID") or props.get("sessionID")
        if our_session and part_session and part_session != our_session:
            return []

        call_id = part.get("callID") or part.get("id", "")
        tool_name = part.get("tool") or part.get("name", "")
        tool_input = part.get("input") or {}
        part_state = part.get("state") or {}
        status = part_state.get("status", "")

        results: list[tuple[str, dict]] = []

        # A "started" / "calling" status = tool invocation begun.
        if status in ("started", "calling", "running"):
            results.append(("tool_call", {
                "tool": tool_name,
                "args": tool_input,
                "call_id": call_id,
            }))
        # A "completed" / "success" status = tool finished with output.
        elif status in ("completed", "success"):
            output = part_state.get("output") or part_state.get("result") or ""
            results.append(("tool_result", {
                "tool": tool_name,
                "result": output,
                "success": True,
                "call_id": call_id,
            }))
        # An "error" status = tool failed.
        elif status in ("error", "failed"):
            err_msg = (
                part_state.get("error")
                or part_state.get("message")
                or f"tool '{tool_name}' failed"
            )
            results.append(("tool_result", {
                "tool": tool_name,
                "result": err_msg,
                "success": False,
                "call_id": call_id,
            }))
        # Unknown status — no emit; caller can re-examine on next update.
        return results

    # -------------------------------------------- native model switched
    if evt_type == "session.next.model.switched":
        # Record for reverse reconcile; do NOT emit a reply kind.
        state["switched_model"] = props.get("model")
        return []

    # -------------------------------------------- turn complete
    if evt_type == "session.idle":
        if not _our():
            return []
        accumulated = state.get("text", "")
        state["done"] = True
        return [("final", {"content": accumulated})]

    # -------------------------------------------- unrecognised
    return []


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OpenCodeAdapter:
    """Drive an opencode headless server for one agent turn at a time.

    Lifecycle:
        1. ``ensure_session()`` — idempotent; creates an opencode session once.
        2. ``prompt(text, record_reply)`` — sends the user turn, streams events,
           calls ``record_reply(kind, **payload)`` for each reply, blocks until
           ``session.idle`` or transport failure.
        3. ``close()`` — shuts down the httpx client.

    ``prompt()`` never raises; transport / server errors degrade to an
    ``error`` reply kind, mirroring the acp_adapter contract.
    """

    def __init__(self, config: OpenCodeConfig, sink) -> None:
        self._cfg = config
        # sink is called once per mapped taOS reply with a dict carrying at
        # least ``kind`` and ``trace_id`` plus kind-specific fields, matching
        # bridge_session.record_reply's body contract (mirrors ACPAdapter).
        self._sink = sink
        self.session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._stream_ctx = None  # holds the active httpx stream context
        # Per-turn mutable state, reset in finally block.
        self._turn_state: dict = {}

    async def _emit(self, reply: dict) -> None:
        """Deliver one reply dict to the sink (sync or async)."""
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("opencode_adapter: sink raised for reply kind=%s", reply.get("kind"))

    # ---------------------------------------------------------------- helpers

    @property
    def _base(self) -> str:
        return self._cfg.base_url.rstrip("/")

    def _auth(self) -> tuple[str, str] | None:
        if self._cfg.server_password:
            return (self._cfg.server_username, self._cfg.server_password)
        return None

    # ------------------------------------------------------------- session

    async def ensure_session(self) -> str:
        """Create the opencode session if one doesn't exist yet.

        Returns the session id.  Idempotent — safe to call multiple times.
        """
        if self.session_id is not None:
            return self.session_id

        client = await self._get_client()
        resp = await client.post(
            f"{self._base}/session",
            json={"title": f"taOS-{self._cfg.agent or 'agent'}"},
        )
        resp.raise_for_status()
        data = resp.json()
        # Spec says id is at .id with fallback .info.id
        session_id = data.get("id") or (data.get("info") or {}).get("id") or ""
        if not session_id:
            raise ValueError(f"opencode /session returned no id: {data!r}")
        self.session_id = session_id
        logger.debug("opencode_adapter: created session %s", session_id)
        return session_id

    # ---------------------------------------------------------------- I/O

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            auth = self._auth()
            self._client = httpx.AsyncClient(
                auth=auth,
                timeout=httpx.Timeout(
                    connect=self._cfg.connect_timeout,
                    read=self._cfg.read_timeout,
                    write=30.0,
                    pool=30.0,
                ),
            )
        return self._client

    async def prompt(
        self,
        text: str,
        trace_id: str | None = None,
    ) -> None:
        """Send *text* to opencode and stream results to the sink.

        Each event that maps to one of the 6 reply kinds is delivered to the
        sink as a dict ``{"kind", "trace_id", ...kind-specific...}``.  ``prompt``
        never raises — any transport or server error results in an ``error``
        reply, mirroring the acp_adapter contract.

        Args:
            text:     User message text.
            trace_id: Optional trace id, forwarded on every emitted reply.
        """
        state: dict = {
            "session_id": self.session_id,
            "text": "",
            "done": False,
            "switched_model": None,
        }
        self._turn_state = state

        async def _emit(kind: str, payload: dict) -> None:
            await self._emit({"kind": kind, "trace_id": trace_id, **payload})

        try:
            # Ensure we have a session before opening the SSE stream.
            await self.ensure_session()
            state["session_id"] = self.session_id

            client = await self._get_client()

            # Open the event stream BEFORE sending the prompt so we don't miss
            # events that fire immediately after prompt_async returns 204.
            async with client.stream(
                "GET",
                f"{self._base}/event",
                timeout=httpx.Timeout(
                    connect=self._cfg.connect_timeout,
                    read=self._cfg.read_timeout,
                    write=30.0,
                    pool=30.0,
                ),
            ) as stream:
                # Now fire the async prompt.
                prompt_resp = await client.post(
                    f"{self._base}/session/{self.session_id}/prompt_async",
                    json={
                        "model": {
                            "providerID": self._cfg.model_provider_id,
                            "modelID": self._cfg.model_id,
                        },
                        "parts": [{"type": "text", "text": text}],
                    },
                )
                if prompt_resp.status_code not in (200, 204):
                    await _emit("error", {
                        "error": (
                            f"opencode prompt_async returned "
                            f"{prompt_resp.status_code}: {prompt_resp.text[:200]}"
                        ),
                    })
                    return

                # Consume SSE lines until session.idle or EOF.
                async for line in stream.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("opencode_adapter: non-JSON SSE line: %r", raw)
                        continue

                    pairs = map_opencode_event(evt, state)
                    for kind, payload in pairs:
                        await _emit(kind, payload)

                    if state.get("done"):
                        break

                # If we exhausted the stream without session.idle → error.
                if not state.get("done"):
                    await _emit("error", {
                        "error": "opencode stream ended before session.idle",
                    })

        except Exception as exc:
            logger.exception("opencode_adapter: prompt() transport error")
            try:
                await _emit("error", {"error": f"opencode transport error: {exc}"})
            except Exception:
                pass
        finally:
            self._turn_state = {}

    # ---------------------------------------------------------------- close

    async def close(self) -> None:
        """Close the underlying httpx client.  Safe to call multiple times."""
        try:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
        except Exception:
            logger.debug("opencode_adapter: error during close", exc_info=True)
        finally:
            self._client = None
