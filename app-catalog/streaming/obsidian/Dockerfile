# TinyAgentOS Streaming App: Obsidian
FROM ubuntu:22.04

LABEL taos.app.id="obsidian-streaming"
LABEL taos.app.version="1.7.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="obsidian-mcp"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 xdg-utils \
    libatspi2.0-0 libsecret-1-0 \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

# Obsidian is an AppImage — downloaded at build or runtime
# RUN wget -O /opt/obsidian.AppImage https://github.com/obsidianmd/obsidian-releases/releases/...

RUN pip3 install --no-cache-dir obsidian-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=obsidian-streaming
ENV TAOS_MCP_SERVER=obsidian-mcp
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
