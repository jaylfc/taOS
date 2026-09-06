# TinyAgentOS Streaming App: Neko Browser
# Based on m1k1o/neko — WebRTC browser streaming
# Upstream image: ghcr.io/m1k1o/neko/firefox:latest
FROM ghcr.io/m1k1o/neko/firefox:latest

LABEL taos.app.id="neko-browser"
LABEL taos.app.version="2.0.0"
LABEL taos.streaming.port="8080"
LABEL taos.bridge.port="9110"
LABEL taos.mcp.server="browser-use-mcp"

# This Dockerfile wraps the upstream Neko image for TinyAgentOS streaming.
# Neko provides a full Firefox browser accessible via WebRTC with multi-user support.
# Agents can control it via browser-use MCP for automated web browsing.

ENV NEKO_SCREEN=1280x720@30
ENV NEKO_PASSWORD=neko
ENV NEKO_PASSWORD_ADMIN=admin

EXPOSE 8080
EXPOSE 52000-52100/udp
