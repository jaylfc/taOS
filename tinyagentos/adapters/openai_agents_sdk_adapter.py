"""OpenAI Agents SDK adapter."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        from agents import Agent, Runner
        agent = Agent(name="assistant", instructions="You are a helpful assistant.")
        result = Runner.run_sync(agent, msg.get("text", ""))
        return {"content": str(result.final_output)}
    except ImportError:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] OpenAI Agents SDK not installed"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "openai-agents-sdk"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TAOS_ADAPTER_PORT", "9001")), log_level="warning")
