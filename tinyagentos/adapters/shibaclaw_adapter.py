"""ShibaClaw adapter — proxies messages to the ShibaClaw gateway."""
import os
import httpx
from fastapi import FastAPI

from tinyagentos.adapters.retry_policy import (
    CONTROLLER_TIMEOUT_SECONDS,
    RETRY_KWARGS,
)
from tinyagentos.clients.retry import with_retry

app = FastAPI()

# Retry settings live in retry_policy so every adapter's worst case stays
# inside the channel-hub router's own timeout.
_RETRY_KWARGS = RETRY_KWARGS


async def _controller_post(url: str, json: dict):
    async with httpx.AsyncClient(timeout=CONTROLLER_TIMEOUT_SECONDS) as client:
        return await client.post(url, json=json)


@app.post("/message")
async def handle_message(msg: dict):
    try:
        sc_url = os.environ.get("SHIBACLAW_URL", "http://localhost:19999")
        resp = await with_retry(
            lambda: _controller_post(f"{sc_url}/api/message", {"text": msg.get("text", "")}),
            **_RETRY_KWARGS,
        )
        if resp.status_code == 200:
            return {"content": resp.json().get("content", resp.text)}
        return {"content": f"ShibaClaw returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] ShibaClaw not available: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "shibaclaw"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
