# TinyAgentOS Streaming App: Grafana
FROM ubuntu:22.04

LABEL taos.app.id="grafana-streaming"
LABEL taos.app.version="11.0.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="grafana-mcp"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg2 apt-transport-https \
    chromium-browser \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

# Grafana OSS
# RUN wget -q -O /usr/share/keyrings/grafana.key https://apt.grafana.com/gpg.key
# RUN echo "deb [signed-by=...] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
# RUN apt-get update && apt-get install -y grafana

RUN pip3 install --no-cache-dir grafana-mcp fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=grafana-streaming
ENV TAOS_MCP_SERVER=grafana-mcp
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
