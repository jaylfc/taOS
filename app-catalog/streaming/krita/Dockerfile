# TinyAgentOS Streaming App: Krita
FROM ubuntu:22.04

LABEL taos.app.id="krita-streaming"
LABEL taos.app.version="5.3.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="krita-mcp"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    krita \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir krita-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=krita-streaming
ENV TAOS_MCP_SERVER=krita-mcp
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
