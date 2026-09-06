"""PocketFlow adapter — translates messages to PocketFlow graph execution calls."""
import os
from fastapi import FastAPI

app = FastAPI()


@app.post("/message")
async def handle_message(msg: dict):
    try:
        from pocketflow import Node, Flow
        import openai

        class ChatNode(Node):
            def prep(self, shared):
                return shared.get("message", "")

            def exec(self, prep_res):
                oai_client = openai.OpenAI()
                response = oai_client.chat.completions.create(
                    model=os.environ.get("TAOS_MODEL", "default"),
                    messages=[{"role": "user", "content": prep_res}],
                )
                return response.choices[0].message.content

            def post(self, shared, prep_res, exec_res):
                shared["response"] = exec_res

        flow = Flow(start=ChatNode())
        shared = {"message": msg.get("text", "")}
        flow.run(shared)
        return {"content": shared.get("response", "")}
    except ImportError:
        return {"content": f"[{os.environ.get('TAOS_AGENT_NAME', 'agent')}] PocketFlow not installed"}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "pocketflow", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
