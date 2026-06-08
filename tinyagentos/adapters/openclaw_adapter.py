"""OpenClaw adapter — translates messages to OpenClaw agent calls via its HTTP API."""
import os
import httpx
from fastapi import FastAPI

from tinyagentos.clients.retry import with_retry

app = FastAPI()

# OpenClaw agents run as LXC containers with their own HTTP endpoints
OPENCLAW_URL = os.environ.get("OPENCLAW_AGENT_URL", "http://localhost:8100")

# Retry settings: cap at ~60s to cover a controller restart window
_RETRY_KWARGS = dict(max_attempts=7, base_delay=0.5, multiplier=2.0, max_delay=60.0)


async def _controller_post(url: str, json: dict):
    """Send a POST to the upstream framework agent. Called via with_retry."""
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(url, json=json)


@app.post("/message")
async def handle_message(msg: dict):
    try:
        resp = await with_retry(
            lambda: _controller_post(
                f"{OPENCLAW_URL}/message",
                {"text": msg.get("text", ""), "from": msg.get("from_name", "User")},
            ),
            **_RETRY_KWARGS,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"content": data.get("response", data.get("content", str(data)))}
        return {"content": f"OpenClaw agent returned status {resp.status_code}"}
    except httpx.ConnectError:
        return {"content": "OpenClaw agent not reachable — is the container running?"}
    except Exception as e:
        return {"content": f"Error: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "openclaw", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
