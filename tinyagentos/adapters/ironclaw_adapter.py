"""IronClaw adapter — proxies messages to the IronClaw gateway."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        import httpx
        ic_url = os.environ.get("IRONCLAW_URL", "http://localhost:8080")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{ic_url}/api/message", json={"text": msg.get("text", "")})
            if resp.status_code == 200:
                return {"content": resp.json().get("content", resp.text)}
            return {"content": f"IronClaw returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] IronClaw not available: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "ironclaw"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
