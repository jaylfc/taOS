"""MicroClaw adapter — proxies messages to the MicroClaw gateway."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        import httpx
        mc_url = os.environ.get("MICROCLAW_URL", "http://localhost:10961")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{mc_url}/api/message", json={"text": msg.get("text", "")})
            if resp.status_code == 200:
                return {"content": resp.json().get("content", resp.text)}
            return {"content": f"MicroClaw returned {resp.status_code}"}
    except Exception as e:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] MicroClaw not available: {e}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "microclaw"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
