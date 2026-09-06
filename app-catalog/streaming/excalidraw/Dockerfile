# TinyAgentOS Streaming App: Excalidraw
FROM ubuntu:22.04

LABEL taos.app.id="excalidraw-streaming"
LABEL taos.app.version="0.17.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="mcp_excalidraw"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    chromium-browser \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir mcp-excalidraw fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=excalidraw-streaming
ENV TAOS_MCP_SERVER=mcp_excalidraw
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
