"""NullClaw adapter — proxies messages to the NullClaw gateway."""
import os
import httpx
from fastapi import FastAPI

from tinyagentos.clients.retry import with_retry

app = FastAPI()

_RETRY_KWARGS = dict(max_attempts=7, base_delay=0.5, multiplier=2.0, max_delay=60.0)


async def _controller_post(url: str, json: dict):
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(url, json=json)


@app.post("/message")
async def handle_message(msg: dict):
    try:
        nc_url = os.environ.get("NULLCLAW_URL", "http://localhost:3000")
        resp = await with_retry(
            lambda: _controller_post(f"{nc_url}/api/message", {"text": msg.get("text", "")}),
            **_RETRY_KWARGS,
        )
        if resp.status_code == 200:
            return {"content": resp.json().get("content", resp.text)}
        return {"content": f"NullClaw returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] NullClaw not available: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "nullclaw"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
