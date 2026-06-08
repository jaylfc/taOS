"""Hermes adapter — proxies messages to the Hermes OpenAI-compatible API server."""
import os
import httpx
from fastapi import FastAPI

from tinyagentos.clients.retry import with_retry

app = FastAPI()

_RETRY_KWARGS = dict(max_attempts=7, base_delay=0.5, multiplier=2.0, max_delay=60.0)


async def _controller_post(url: str, json: dict, headers: dict):
    async with httpx.AsyncClient(timeout=120) as client:
        return await client.post(url, json=json, headers=headers)


@app.post("/message")
async def handle_message(msg: dict):
    try:
        hermes_url = os.environ.get("HERMES_API_URL", "http://localhost:8642")
        hermes_key = os.environ.get("HERMES_API_KEY", "")
        headers = {"Authorization": f"Bearer {hermes_key}"}
        resp = await with_retry(
            lambda: _controller_post(
                f"{hermes_url}/v1/chat/completions",
                {"model": "hermes", "messages": [{"role": "user", "content": msg.get("text", "")}]},
                headers,
            ),
            **_RETRY_KWARGS,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"content": data["choices"][0]["message"]["content"]}
        return {"content": f"Hermes returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] Hermes not available: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "hermes"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
