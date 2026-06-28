"""DeerFlow adapter — proxies messages to the DeerFlow LangGraph runs API."""
import os
import httpx
from fastapi import FastAPI

from tinyagentos.clients.retry import with_retry

app = FastAPI()

# Retry ONLY on connection-level failures (the service is still starting, or a
# transient transport blip) — NOT on ReadTimeout. /api/runs/wait drives a
# long-horizon agent that legitimately takes minutes; a read timeout means the
# run is in progress, so re-firing it would re-execute the whole job (and any
# side effects). Keep the attempt count low for the same reason.
_RETRY_ON = (httpx.ConnectError, httpx.RemoteProtocolError)
_RETRY_KWARGS = dict(
    max_attempts=3, base_delay=0.5, multiplier=2.0, max_delay=10.0, retry_on=_RETRY_ON
)


async def _controller_post(url: str, json: dict):
    # 600s timeout — DeerFlow is a long-horizon multi-step agent.
    async with httpx.AsyncClient(timeout=600) as client:
        return await client.post(url, json=json)


def _msg_content_text(msg) -> str:
    """Render one message's content as plain text.

    Handles {"content": str} and LangChain {"content": [{"type":"text",...}]}.
    """
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _extract_text(messages) -> str:
    """Pull the assistant reply from a list of LangGraph/LangChain messages.

    The final channel-values message is not necessarily the assistant turn —
    after a tool call it can be a tool/human message — so scan from the end for
    the last assistant/AI message rather than blindly taking ``messages[-1]``.
    Handles plain {"role": "assistant", "content": str} and LangChain
    {"type": "ai", "content": [...]} shapes. Falls back to the last message's
    text, then str() of it.
    """
    if not messages:
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or msg.get("type")
        if role in ("assistant", "ai", "AIMessage"):
            text = _msg_content_text(msg)
            if text:
                return text
    # No assistant message found — fall back to the last message's text.
    return _msg_content_text(messages[-1]) or str(messages[-1])


@app.post("/message")
async def handle_message(msg: dict):
    agent_name = os.environ.get("TAOS_AGENT_NAME", "agent")
    try:
        deer_flow_url = os.environ.get("DEER_FLOW_URL", "http://localhost:8001")
        resp = await with_retry(
            lambda: _controller_post(
                f"{deer_flow_url}/api/runs/wait",
                {
                    "input": {"messages": [{"role": "user", "content": msg.get("text", "")}]},
                    "context": {"model_name": "taos-default"},
                },
            ),
            **_RETRY_KWARGS,
        )
        if resp.status_code == 200:
            data = resp.json()
            # The body is the final serialized channel-values. The messages list
            # may sit at the top level or under output/values — try each.
            messages = (
                data.get("messages")
                or (data.get("output") or {}).get("messages")
                or (data.get("values") or {}).get("messages")
                or []
            )
            return {"content": _extract_text(messages)}
        return {"content": f"[{agent_name}] DeerFlow returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{agent_name}] DeerFlow unavailable: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "deer-flow"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
