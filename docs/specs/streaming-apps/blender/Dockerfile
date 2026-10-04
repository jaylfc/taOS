# TinyAgentOS Streaming App: Blender
# Base image provides KasmVNC + agent-bridge + xdotool
# For now, this is a specification — actual build requires taos-app-base image
FROM ubuntu:22.04

LABEL taos.app.id="blender-streaming"
LABEL taos.app.version="4.3.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="blender-mcp"

# This Dockerfile is a reference spec for Plan 4 (worker distribution)
# which builds the actual taos-app-base image. For now it documents
# what the container needs.

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    blender \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir blender-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=blender-streaming
ENV TAOS_MCP_SERVER=blender-mcp
EXPOSE 6901 9100

# In production: startup script starts KasmVNC + Blender + agent-bridge
CMD ["echo", "Container spec — see Plan 4 for full runtime"]
