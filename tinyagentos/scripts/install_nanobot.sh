#!/bin/bash
# Install nanobot (HKUDS/nanobot) inside an LXC agent container + the
# taOS-nanobot bridge. nanobot's OpenAI-compatible API server runs in the
# foreground via `nanobot serve` so systemd manages it. Provider, key and
# model are rendered into /root/.nanobot/config.json at install time.
set -euo pipefail

log() { echo "[$(date -u +%H:%M:%S)] nanobot-install: $*"; }

AGENT_NAME="${TAOS_AGENT_NAME:?TAOS_AGENT_NAME required}"
LLM_KEY="${LITELLM_API_KEY:?LITELLM_API_KEY required}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?TAOS_BRIDGE_URL required}"
LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?TAOS_LOCAL_TOKEN required}"
MODEL="${TAOS_MODEL:-kilo-auto/free}"
LLM_BASE="${OPENAI_BASE_URL:-http://127.0.0.1:4000/v1}"

log "installing nanobot-ai==0.3.5 (requires Python >=3.11)"
pip3 install --break-system-packages --quiet nanobot-ai==0.3.5 httpx 2>&1 | tail -3
NANOBOT_BIN="$(command -v nanobot || true)"
[ -z "$NANOBOT_BIN" ] && { log "ERROR: nanobot binary not found"; exit 2; }
log "nanobot at $NANOBOT_BIN"

log "writing /root/.nanobot/config.json"
mkdir -p /root/.nanobot
# Read values via os.environ so shell-special characters can't break the
# python literal; json.dump handles quoting in the rendered file.
TAOS_MODEL_ENV="$MODEL" TAOS_LLM_KEY_ENV="$LLM_KEY" TAOS_LLM_BASE_ENV="$LLM_BASE" python3 - <<'PYEOF'
import json, os
cfg = {
    "providers": {
        "vllm": {
            "apiBase": os.environ["TAOS_LLM_BASE_ENV"],
            "apiKey": os.environ["TAOS_LLM_KEY_ENV"],
        },
    },
    "modelPresets": {
        "primary": {"provider": "vllm", "model": os.environ["TAOS_MODEL_ENV"]},
    },
    "agents": {"defaults": {"modelPreset": "primary"}},
    "api": {"host": "127.0.0.1", "port": 8900},
}
with open("/root/.nanobot/config.json", "w") as f:
    json.dump(cfg, f, indent=2)
print("config written OK")
PYEOF
chmod 600 /root/.nanobot/config.json

log "enabling nanobot api plugin"
# The plugin installs aiohttp with pip; the base image is Debian (PEP 668).
PIP_BREAK_SYSTEM_PACKAGES=1 "$NANOBOT_BIN" plugins enable api

cat > /root/.nanobot/taos.env <<ENVEOF
TAOS_AGENT_NAME=$AGENT_NAME
TAOS_MODEL=$MODEL
TAOS_LOCAL_TOKEN=$LOCAL_TOKEN
TAOS_BRIDGE_URL=$BRIDGE_URL
LITELLM_API_KEY=$LLM_KEY
OPENAI_API_KEY=$LLM_KEY
OPENAI_BASE_URL=$LLM_BASE
ENVEOF
chmod 600 /root/.nanobot/taos.env

log "creating systemd unit for nanobot API server (foreground)"
cat > /etc/systemd/system/nanobot-api.service <<UNIT
[Unit]
Description=nanobot OpenAI-compatible API server (foreground)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root
EnvironmentFile=/root/.nanobot/taos.env
ExecStart=$NANOBOT_BIN serve
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/nanobot-api.log
StandardError=append:/var/log/nanobot-api.log

[Install]
WantedBy=multi-user.target
UNIT

log "writing taOS-nanobot bridge"
mkdir -p /opt/taos
cat > /opt/taos/taos-nanobot-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-nanobot bridge: subscribes to taOS SSE for this agent, forwards
user messages to the local nanobot API server (/v1/chat/completions) at
127.0.0.1:8900, and POSTs replies back to taOS via the openclaw reply
URL. Lets nanobot participate in chat through the existing
agent_chat_router -> bridge_session pipeline that openclaw uses today,
no taOS-side changes required.

nanobot's endpoint accepts exactly one user message per request and
rejects any model other than its configured preset, so the bridge folds
the instructions, recent context and latest message into one user
message, omits "model", and keys the session as <agent>-<channel>.

Env (injected by deployer): TAOS_BRIDGE_URL, TAOS_AGENT_NAME,
TAOS_LOCAL_TOKEN.

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [nanobot-bridge] %(message)s")
log = logging.getLogger("nanobot-bridge")

BRIDGE_URL = os.environ["TAOS_BRIDGE_URL"]
AGENT_NAME = os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN = os.environ["TAOS_LOCAL_TOKEN"]
NANOBOT_URL = os.environ.get("NANOBOT_URL", "http://127.0.0.1:8900")
RECONNECT_DELAY = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds, grows to 2, 4, 8 for retries 1-3
ERROR_COOLDOWN = 5.0      # seconds after a final error before accepting new messages

_MAX_INFLIGHT = 4
_SEMAPHORE = asyncio.Semaphore(_MAX_INFLIGHT)


async def fetch_bootstrap(client: httpx.AsyncClient) -> dict:
    url = f"{BRIDGE_URL}/api/openclaw/bootstrap?agent={AGENT_NAME}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, timeout=30)
    resp.raise_for_status()
    boot = resp.json()
    if boot.get("schema_version") != 1:
        raise RuntimeError(f"unsupported bootstrap schema_version={boot.get('schema_version')}")
    return boot


_SYSTEM_PROMPT = (
    f"You are {AGENT_NAME}, an autonomous agent running inside nanobot "
    "(HKUDS/nanobot) deployed on taOS. When asked what framework you run "
    "on, say nanobot. The underlying language model is routed through "
    "taOS's LiteLLM proxy and is an implementation detail, do not describe yourself as Claude/GPT/etc. just because the model "
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

async def _run_handle_user_message_limited(
    client: httpx.AsyncClient,
    evt: dict,
    channel: dict,
    _seen: set,
    _error_until: list,
) -> bool:
    async with _SEMAPHORE:
        return await handle_user_message(client, evt, channel, _seen, _error_until)


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


async def call_nanobot(client: httpx.AsyncClient, content: str, session_id: str) -> str:
    """Call nanobot's OpenAI-compatible /v1/chat/completions and return the
    assistant's reply text. nanobot takes exactly one user message and its
    own fixed model preset, so the body carries a single user message and a
    session_id and never a "model" key. Retries with exponential backoff on
    transient failures; returns a short error string if all attempts fail so
    the user always sees something."""
    payload = {
        "messages": [{"role": "user", "content": content}],
        "session_id": session_id,
    }
    headers = {"Content-Type": "application/json"}
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(f"{NANOBOT_URL}/v1/chat/completions",
                                      json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # Non-200: 5xx are retryable, 4xx are not
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("nanobot %d (attempt %d/%d), retry in %.1fs",
                            resp.status_code, attempt, MAX_RETRIES, delay)
                last_error = f"[nanobot returned {resp.status_code}: {resp.text[:200]}]"
                await asyncio.sleep(delay)
                continue
            return f"[nanobot returned {resp.status_code}: {resp.text[:200]}]"
        except Exception as e:
            detail = str(e) or type(e).__name__
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("nanobot error %s (attempt %d/%d), retry in %.1fs",
                            detail, attempt, MAX_RETRIES, delay)
                last_error = f"[nanobot error: {detail}]"
                await asyncio.sleep(delay)
                continue
            return f"[nanobot error: {detail}]"
    return last_error or "[nanobot error: unknown]"


async def post_reply(client: httpx.AsyncClient, reply_url: str, token: str,
                     msg_id: str, trace_id: str, content: str, cid=None) -> bool:
    body = {"kind": "final", "id": msg_id, "trace_id": trace_id, "content": content}
    if cid: body["channel_id"] = cid
    try:
        resp = await client.post(reply_url, json=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"},
                                  timeout=30)
        if resp.status_code >= 400:
            log.warning("reply POST %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as e:
        log.warning("reply POST failed: %s", e)
        return False


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
        log.info("user_message id=%s already processed, skipping", msg_id)
        return False

    # Error cooldown: after a final failure, pause before accepting new messages
    now = asyncio.get_event_loop().time()
    if now < _error_until[0]:
        log.info("user_message id=%s suppressed during error cooldown (%.1fs remaining)",
                 msg_id, _error_until[0] - now)
        return False

    log.info("user_message id=%s text=%r force=%s", msg_id, text[:80], force)
    system = _SYSTEM_PROMPT + ("\n\nYou were directly addressed. Reply naturally; do not output NO_RESPONSE."
        if force else
        "\n\nIf you were not explicitly @mentioned and this message is not for you, reply with exactly: NO_RESPONSE\nOtherwise reply naturally. Keep it short in group chats.")
    # nanobot accepts exactly one user message, so fold everything into it.
    parts = [system]
    if ctx:
        parts.append(f"Recent conversation:\n{ctx}")
    parts.append(text)
    if attach_line:
        parts.append(attach_line)
    content = "\n\n".join(parts)
    session_id = f"{AGENT_NAME}-{cid}" if cid else AGENT_NAME
    await _thinking(client, cid, "start")
    try:
        reply = await call_nanobot(client, content, session_id)
    finally:
        await _thinking(client, cid, "end")
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed NO_RESPONSE for id=%s", msg_id)
        return False

    # If the reply is an error, start the cooldown to prevent tight retry loops
    if final.startswith("[nanobot "):
        log.warning("nanobot error reply for id=%s, enabling %.1fs cooldown", msg_id, ERROR_COOLDOWN)
        _error_until[0] = asyncio.get_event_loop().time() + ERROR_COOLDOWN

    # Only mark as seen after successful reply POST
    posted = await post_reply(client, channel["reply_url"], channel["auth_bearer"],
                     msg_id, trace_id, final, cid)
    if posted and msg_id:
        if len(_seen) >= _MAX_SEEN:
            # Discard oldest half to keep memory bounded
            _seen.clear()
        _seen.add(msg_id)
    return posted


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
                    log.warning("SSE %s, retry", resp.status_code)
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
                                task = asyncio.create_task(_run_handle_user_message_limited(
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
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient() as client:
        # Wait for the nanobot API server to be healthy
        for i in range(40):
            try:
                r = await client.get(f"{NANOBOT_URL}/health", timeout=5)
                if r.status_code == 200:
                    log.info("nanobot API server healthy")
                    break
            except Exception:
                pass
            await asyncio.sleep(3)
        else:
            log.warning("nanobot API server health never returned 200; continuing anyway")

        boot = await fetch_bootstrap(client)
        channel = boot["channel"]
        log.info("bootstrap OK: agent=%s session=%s", boot.get("agent_name"), boot.get("session_id"))
        await sse_loop(client, channel, stop)


if __name__ == "__main__":
    asyncio.run(main())
BRIDGE_EOF
chmod +x /opt/taos/taos-nanobot-bridge.py

cat > /etc/systemd/system/taos-nanobot-bridge.service <<UNIT
[Unit]
Description=taOS-nanobot bridge (SSE -> nanobot /v1/chat/completions)
After=nanobot-api.service network.target
Wants=nanobot-api.service
[Service]
Type=simple
EnvironmentFile=/root/.nanobot/taos.env
Environment=NANOBOT_URL=http://127.0.0.1:8900
ExecStart=/usr/bin/python3 /opt/taos/taos-nanobot-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-nanobot-bridge.log
StandardError=append:/var/log/taos-nanobot-bridge.log
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable nanobot-api.service
systemctl start nanobot-api.service

log "waiting for nanobot :8900 (up to 90s)"
for i in $(seq 1 30); do
    sleep 3
    if curl -fsS http://127.0.0.1:8900/health > /dev/null 2>&1; then
        log "nanobot API server ready"
        break
    fi
done

systemctl enable --now taos-nanobot-bridge.service
mkdir -p /opt/taos
echo "nanobot-0.3.5" > /opt/taos/framework.version
log "done"
