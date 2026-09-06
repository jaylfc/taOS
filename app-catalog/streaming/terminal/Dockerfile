# TinyAgentOS Streaming App: Terminal
FROM ubuntu:22.04

LABEL taos.app.id="terminal-streaming"
LABEL taos.app.version="1.0.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="desktop-commander-mcp"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    xfce4-terminal \
    nodejs npm \
    python3 python3-pip \
    xdotool scrot xclip \
    curl wget git \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=terminal-streaming
ENV TAOS_MCP_SERVER=desktop-commander-mcp
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
