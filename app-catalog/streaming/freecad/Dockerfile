# TinyAgentOS Streaming App: FreeCAD
FROM ubuntu:22.04

LABEL taos.app.id="freecad-streaming"
LABEL taos.app.version="1.0.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="freecad-mcp"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    freecad \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir freecad-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=freecad-streaming
ENV TAOS_MCP_SERVER=freecad-mcp
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
