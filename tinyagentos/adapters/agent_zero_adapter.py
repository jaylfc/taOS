"""Agent Zero adapter — proxies messages to the Agent Zero HTTP API."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        import httpx
        a0_url = os.environ.get("AGENT_ZERO_URL", "http://localhost:8080")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{a0_url}/message",
                json={"text": msg.get("text", "")},
                headers={"X-API-KEY": os.environ.get("AGENT_ZERO_API_KEY", "")},
            )
            if resp.status_code == 200:
                return {"content": resp.json().get("response", resp.text)}
            return {"content": f"Agent Zero returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] Agent Zero error: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "agent_zero"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
