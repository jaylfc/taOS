"""SmolAgents adapter — translates messages to SmolAgents code agent calls."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        from smolagents import CodeAgent, OpenAIModel
        model = OpenAIModel(
            model_id=os.environ.get("TAOS_MODEL", "default"),
            api_base=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
        agent = CodeAgent(tools=[], model=model)
        result = agent.run(msg.get("text", ""))
        return {"content": str(result)}
    except ImportError:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] SmolAgents not installed"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "smolagents", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
