# TinyAgentOS Streaming App: GIMP
# Base image provides KasmVNC + agent-bridge + xdotool
# For now, this is a specification — actual build requires taos-app-base image
FROM ubuntu:22.04

LABEL taos.app.id="gimp-streaming"
LABEL taos.app.version="3.0.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="gimp-mcp"

# This Dockerfile is a reference spec for Plan 4 (worker distribution)
# which builds the actual taos-app-base image. For now it documents
# what the container needs.

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    gimp \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir gimp-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=gimp-streaming
ENV TAOS_MCP_SERVER=gimp-mcp
EXPOSE 6901 9100

# In production: startup script starts KasmVNC + GIMP + agent-bridge
CMD ["echo", "Container spec — see Plan 4 for full runtime"]
