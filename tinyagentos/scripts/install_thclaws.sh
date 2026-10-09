#!/bin/bash
# Install thClaws inside an LXC agent container + the taOS-thClaws bridge.
# thClaws runs in foreground via `thclaws --serve` so systemd manages it.
set -euo pipefail

log() { echo "[$(date -u +%H:%M:%S)] thclaws-install: $*"; }

VERSION="0.140.0"

AGENT_NAME="${TAOS_AGENT_NAME:?TAOS_AGENT_NAME required}"
LLM_KEY="${LITELLM_API_KEY:?LITELLM_API_KEY required}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?TAOS_BRIDGE_URL required}"
LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?TAOS_LOCAL_TOKEN required}"
MODEL="${TAOS_MODEL:-kilo-auto/free}"

log "installing required libraries for thClaws (webkit2gtk/wayland deps)"
apt-get update
apt-get install -y --no-install-recommends \
    libwayland-client0 \
    libwebkit2gtk-4.1-0 \
    libsoup-3.0-0 \
    libssl3 \
    ca-certificates \
    curl

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  TARCH="x86_64" ;;
    aarch64) TARCH="aarch64" ;;
    *) log "ERROR: unsupported arch $ARCH"; exit 1 ;;
esac

TARBALL="thclaws-v${VERSION}-${TARCH}-unknown-linux-gnu.tar.gz"
SHA256_FILE="${TARBALL}.sha256"
BASE_URL="https://github.com/thClaws/thClaws/releases/download/v${VERSION}"

log "downloading thClaws $VERSION for $TARCH"
cd /tmp
curl -fL -O "${BASE_URL}/${TARBALL}"
curl -fL -O "${BASE_URL}/${SHA256_FILE}"

log "verifying checksum"
sha256sum -c "${SHA256_FILE}"

log "installing thclaws and thclaws-cli to /usr/local/bin"
tar -xzf "${TARBALL}"
install -m 755 thclaws /usr/local/bin/thclaws
install -m 755 thclaws-cli /usr/local/bin/thclaws-cli

log "creating systemd unit for thClaws server (foreground / serve mode)"
cat > /etc/systemd/system/thclaws-serve.service <<UNIT
[Unit]
Description=thClaws Agent Server (foreground / serve mode)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root
EnvironmentFile=/etc/thclaws/env
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/local/bin/thclaws --serve --port 8443 --bind 127.0.0.1
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/thclaws-serve.log
StandardError=append:/var/log/thclaws-serve.log

[Install]
WantedBy=multi-user.target
UNIT

log "writing thClaws environment file with LiteLLM config"
mkdir -p /etc/thclaws
cat > /etc/thclaws/env <<ENVEOF
LITELLM_BASE_URL=${OPENAI_BASE_URL:-http://127.0.0.1:4000/v1}
LITELLM_API_KEY=${LITELLM_API_KEY}
ENVEOF
chmod 600 /etc/thclaws/env

log "writing taOS-thClaws bridge"
mkdir -p /opt/taos
pip3 install --break-system-packages --quiet httpx 2>&1 | tail -3
cat > /opt/taos/taos-thclaws-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-thClaws bridge: subscribes to taOS SSE for this agent, forwards
user messages to the local thClaws server (/v1/chat/completions) at
127.0.0.1:8443, and POSTs replies back to taOS via the openclaw reply
URL. Lets thClaws participate in chat through the existing
agent_chat_router -> bridge_session pipeline that openclaw uses today --
no taOS-side changes required.

Env (injected by deployer): TAOS_BRIDGE_URL, TAOS_AGENT_NAME,
TAOS_LOCAL_TOKEN, LITELLM_API_KEY (optional, for thclaws auth).

Stdlib + httpx only. No openclaw coupling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [thclaws-bridge] %(message)s")
log = logging.getLogger("thclaws-bridge")

BRIDGE_URL = os.environ["TAOS_BRIDGE_URL"]
AGENT_NAME = os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN = os.environ["TAOS_LOCAL_TOKEN"]
THCLAWS_URL = os.environ.get("THCLAWS_URL", "http://127.0.0.1:8443")
THCLAWS_KEY = os.environ.get("LITELLM_API_KEY", "")
THCLAWS_MODEL = os.environ.get("TAOS_MODEL", "kilo-auto/free")
RECONNECT_DELAY = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds -- grows to 2, 4, 8 for retries 1-3
ERROR_COOLDOWN = 5.0      # seconds after a final error before accepting new messages


async def fetch_bootstrap(client: httpx.AsyncClient) -> dict:
    url = f"{BRIDGE_URL}/api/openclaw/bootstrap?agent={AGENT_NAME}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, timeout=30)
    resp.raise_for_status()
    boot = resp.json()
    if boot.get("schema_version") != 1:
        raise RuntimeError(f"unsupported bootstrap schema_version={boot.get('schema_version')}")
    return boot


_SYSTEM_PROMPT = (
    f"You are {AGENT_NAME}, an autonomous agent running inside thClaws "
    "(thClaws/thClaws) deployed on taOS. When asked "
    "what framework you run on, say thClaws. The underlying language model "
    "is routed through taOS's LiteLLM proxy and is an implementation detail "
    "-- do not describe yourself as Claude/GPT/etc. just because the model "
    "weights come from Anthropic or OpenAI."
)


def _render_context(ctx):
    if not ctx:
        return ""
    lines = []
    for m in ctx:
        who = m.get("author_id") or "?"
        lines.append(f"{who}: {m.get('content','')}")
    return "\n".join(lines)


def _render_attachments(atts):
    if not atts:
        return ""
    parts = []
    for a in atts:
        size_kb = max(1, int(a.get("size", 0) / 1024))
        parts.append(f"{a.get('filename','file')} ({a.get('mime_type','?')}, {size_kb} KB)")
    return "User attached: " + ", ".join(parts)


def _suppress(reply, force):
    if force:
        return reply
    stripped = (reply or "").strip().lower().strip(".!,;:")
    return None if stripped == "no_response" else reply


async def _thinking(c: httpx.AsyncClient, ch_id, state: str, *,
                   phase: str | None = None, detail: str | None = None) -> None:
    if not ch_id:
        return
    body = {"slug": AGENT_NAME, "state": state}
    if phase is not None:
        body["phase"] = phase
    if detail is not None:
        body["detail"] = detail
    try:
        await c.post(
            f"{BRIDGE_URL}/api/chat/channels/{ch_id}/thinking",
            json=body,
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            timeout=5,
        )
    except Exception:
        pass  # best-effort; never block a reply on an indicator


async def call_thclaws(client: httpx.AsyncClient, messages: list) -> str:
    """Call thClaws' OpenAI-compatible /v1/chat/completions and return the
    assistant's reply text. Retries with exponential backoff on transient
    failures; returns a short error string if all attempts fail so the
    user always sees something."""
    payload = {
        "model": "litellm/" + THCLAWS_MODEL,
        "messages": messages,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if THCLAWS_KEY:
        headers["Authorization"] = f"Bearer {THCLAWS_KEY}"
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(f"{THCLAWS_URL}/v1/chat/completions",
                                      json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # Non-200: 5xx are retryable, 4xx are not
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("thclaws %d (attempt %d/%d) -- retry in %.1fs",
                            resp.status_code, attempt, MAX_RETRIES, delay)
                last_error = f"[thclaws returned {resp.status_code}: {resp.text[:200]}]"
                await asyncio.sleep(delay)
                continue
            return f"[thclaws returned {resp.status_code}: {resp.text[:200]}]"
        except Exception as e:
            detail = str(e) or type(e).__name__
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("thclaws error %s (attempt %d/%d) -- retry in %.1fs",
                            detail, attempt, MAX_RETRIES, delay)
                last_error = f"[thclaws error: {detail}]"
                await asyncio.sleep(delay)
                continue
            return f"[thclaws error: {detail}]"
    return last_error or "[thclaws error: unknown]"


async def post_reply(client: httpx.AsyncClient, reply_url: str, token: str,
                     msg_id: str, trace_id: str, content: str, cid=None) -> None:
    body = {"kind": "final", "id": msg_id, "trace_id": trace_id, "content": content}
    if cid: body["channel_id"] = cid
    try:
        resp = await client.post(reply_url, json=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"},
                                  timeout=30)
        if resp.status_code >= 400:
            log.warning("reply POST %s: %s", resp.status_code, resp.text[:300])
    except Exception as e:
        log.warning("reply POST failed: %s", e)


async def handle_user_message(client: httpx.AsyncClient, evt: dict, channel: dict,
                              _seen: set, _error_until: list) -> bool:
    """Process one user_message event. Returns True if a reply was posted.
    Deduplicates by msg_id and enforces an error cooldown to prevent
    runaway retry loops driven by repeated SSE events."""
    msg_id = evt.get("id", "")
    trace_id = evt.get("trace_id", msg_id)
    text = evt.get("text", "")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    attach_line = _render_attachments(evt.get("attachments") or [])
    cid = evt.get("channel_id")

    # Dedup: never re-process the same message.
    # Bound the set to prevent unbounded growth over very long-lived SSE sessions.
    _MAX_SEEN = 1000
    if msg_id and msg_id in _seen:
        log.info("user_message id=%s already processed -- skipping", msg_id)
        return False
    if msg_id:
        if len(_seen) >= _MAX_SEEN:
            # Discard oldest half to keep memory bounded
            _seen.clear()
        _seen.add(msg_id)

    # Error cooldown: after a final failure, pause before accepting new messages
    now = asyncio.get_running_loop().time()
    if now < _error_until[0]:
        log.info("user_message id=%s suppressed during error cooldown (%.1fs remaining)",
                 msg_id, _error_until[0] - now)
        return False

    log.info("user_message id=%s text=%r force=%s", msg_id, text[:80], force)
    system = _SYSTEM_PROMPT + ("\n\nYou were directly addressed. Reply naturally; do not output NO_RESPONSE."
        if force else
        "\n\nIf you were not explicitly @mentioned and this message is not for you, reply with exactly: NO_RESPONSE\nOtherwise reply naturally. Keep it short in group chats.")
    messages = [{"role": "system", "content": system}]
    if ctx:
        messages.append({"role": "user", "content": f"Recent conversation:\n{ctx}"})
    messages.append({"role": "user", "content": text})
    if attach_line:
        messages.append({"role": "user", "content": attach_line})
    await _thinking(client, cid, "start")
    try:
        reply = await call_thclaws(client, messages)
    finally:
        await _thinking(client, cid, "end")
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed NO_RESPONSE for id=%s", msg_id)
        return False

    # If the reply is an error, start the cooldown to prevent tight retry loops
    if final.startswith("[thclaws "):
        log.warning("thclaws error reply for id=%s -- enabling %.1fs cooldown", msg_id, ERROR_COOLDOWN)
        _error_until[0] = asyncio.get_running_loop().time() + ERROR_COOLDOWN

    await post_reply(client, channel["reply_url"], channel["auth_bearer"],
                     msg_id, trace_id, final, cid)
    return True


async def sse_loop(client: httpx.AsyncClient, channel: dict, stop: asyncio.Event) -> None:
    seen_ids: set[str] = set()
    error_until: list[float] = [0.0]  # mutable so tasks can update it
    pending_tasks: set[asyncio.Task] = set()
    while not stop.is_set():
        try:
            log.info("SSE connecting to %s", channel["events_url"])
            async with client.stream("GET", channel["events_url"],
                                      headers={"Authorization": f"Bearer {channel['auth_bearer']}",
                                               "Accept": "text/event-stream",
                                               "Cache-Control": "no-cache"},
                                      timeout=None) as resp:
                if resp.status_code != 200:
                    log.warning("SSE %s -- retry", resp.status_code)
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                log.info("SSE connected")
                evt_type = ""
                evt_data = ""
                async for raw in resp.aiter_lines():
                    if stop.is_set():
                        break
                    if raw == "":
                        if evt_type == "user_message" and evt_data:
                            try:
                                evt = json.loads(evt_data)
                                task = asyncio.create_task(handle_user_message(
                                    client, evt, channel, seen_ids, error_until))
                                pending_tasks.add(task)
                                task.add_done_callback(pending_tasks.discard)
                            except Exception as e:
                                log.warning("parse error: %s", e)
                        evt_type, evt_data = "", ""
                        continue
                    if raw.startswith(":"):
                        continue
                    if raw.startswith("event:"):
                        evt_type = raw[6:].strip()
                    elif raw.startswith("data:"):
                        evt_data = raw[5:].lstrip()
        except Exception as e:
            log.warning("SSE error: %s; retry in %ds", e, RECONNECT_DELAY)
        if not stop.is_set():
            await asyncio.sleep(RECONNECT_DELAY)
    # Wait for any in-flight message handlers to complete before exiting
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient() as client:
        boot = await fetch_bootstrap(client)
        channel = boot["channel"]
        log.info("bootstrap OK: agent=%s session=%s", boot.get("agent_name"), boot.get("session_id"))
        await sse_loop(client, channel, stop)


if __name__ == "__main__":
    asyncio.run(main())
BRIDGE_EOF
chmod +x /opt/taos/taos-thclaws-bridge.py

cat > /etc/systemd/system/taos-thclaws-bridge.service <<UNIT
[Unit]
Description=taOS-thClaws bridge (SSE -> thClaws /v1/chat/completions)
After=thclaws-serve.service network.target
Wants=thclaws-serve.service
[Service]
Type=simple
Environment=TAOS_BRIDGE_URL=$BRIDGE_URL
Environment=TAOS_AGENT_NAME=$AGENT_NAME
Environment=TAOS_LOCAL_TOKEN=$LOCAL_TOKEN
Environment=LITELLM_API_KEY=$LLM_KEY
Environment=TAOS_MODEL=$MODEL
Environment=THCLAWS_URL=http://127.0.0.1:8443
ExecStart=/usr/bin/python3 /opt/taos/taos-thclaws-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-thclaws-bridge.log
StandardError=append:/var/log/taos-thclaws-bridge.log
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable thclaws-serve.service
systemctl start thclaws-serve.service

systemctl enable --now taos-thclaws-bridge.service
mkdir -p /opt/taos
echo "thclaws-${VERSION}" > /opt/taos/framework.version
log "done"
