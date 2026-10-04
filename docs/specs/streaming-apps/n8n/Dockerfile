# TinyAgentOS Streaming App: n8n
FROM ubuntu:22.04

LABEL taos.app.id="n8n-streaming"
LABEL taos.app.version="1.70.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="n8n-api"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    chromium-browser \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g n8n@1.70.0

RUN pip3 install --no-cache-dir fastapi uvicorn httpx

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=n8n-streaming
ENV TAOS_MCP_SERVER=n8n-api
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
