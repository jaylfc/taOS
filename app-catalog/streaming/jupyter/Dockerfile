# TinyAgentOS Streaming App: JupyterLab
FROM ubuntu:22.04

LABEL taos.app.id="jupyter-streaming"
LABEL taos.app.version="4.3.0"
LABEL taos.streaming.port="6901"
LABEL taos.bridge.port="9100"
LABEL taos.mcp.server="jupyter-mcp-server"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    xdotool scrot xclip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    jupyterlab==4.3.* \
    jupyter-mcp-server \
    pandas numpy matplotlib scikit-learn \
    fastapi uvicorn

COPY agent_bridge.py /opt/taos/agent_bridge.py

ENV TAOS_APP_ID=jupyter-streaming
ENV TAOS_MCP_SERVER=jupyter-mcp-server
EXPOSE 6901 9100

CMD ["echo", "Container spec — see Plan 4 for full runtime"]
