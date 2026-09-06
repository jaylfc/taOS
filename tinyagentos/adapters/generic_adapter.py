"""Generic adapter — receives messages, calls framework, returns response."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    # Generic: just echo back with a note that no framework-specific adapter exists
    return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] Received: {msg.get('text', '')}"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "generic", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
