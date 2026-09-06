# TinyAgentOS Streaming App: Code Server
# Base image provides KasmVNC + agent-bridge + xdotool
# For now, this is a specification — actual build requires taos-app-base image
FROM ubuntu:22.04

LABEL taos.app.id="code-server-streaming"
LABEL taos.app.version="4.96.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="vscode-native-mcp"

# This Dockerfile is a reference spec for Plan 4 (worker distribution)
# which builds the actual taos-app-base image. For now it documents
# what the container needs.

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

# Install code-server
RUN curl -fsSL https://code-server.dev/install.sh | sh

RUN pip3 install --no-cache-dir fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=code-server-streaming
ENV TAOS_MCP_SERVER=vscode-native-mcp
EXPOSE 6901 9100

# In production: startup script starts KasmVNC + code-server + agent-bridge
CMD ["echo", "Container spec — see Plan 4 for full runtime"]
