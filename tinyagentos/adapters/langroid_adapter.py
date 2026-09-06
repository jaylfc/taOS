"""Langroid adapter — translates messages to Langroid ChatAgent calls."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        import langroid as lr
        agent = lr.ChatAgent(lr.ChatAgentConfig(
            llm=lr.language_models.OpenAIGPTConfig(
                chat_model=os.environ.get("TAOS_MODEL", "default"),
            ),
        ))
        result = agent.llm_response(msg.get("text", ""))
        return {"content": result.content if result else "No response"}
    except ImportError:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] Langroid not installed"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "langroid", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
