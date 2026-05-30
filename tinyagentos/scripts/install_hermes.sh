#!/bin/bash
# Install Hermes inside an LXC agent container + the taOS-Hermes bridge.
# Hermes runs in foreground via `gateway run` so systemd manages it.
# api_key lives in /root/.hermes/config.yaml — Hermes' credential pool reads
# from there for `provider: custom` (env var alone is not sufficient).
set -euo pipefail

log() { echo "[$(date -u +%H:%M:%S)] hermes-install: $*"; }

AGENT_NAME="${TAOS_AGENT_NAME:?TAOS_AGENT_NAME required}"
LLM_KEY="${LITELLM_API_KEY:?LITELLM_API_KEY required}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?TAOS_BRIDGE_URL required}"
LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?TAOS_LOCAL_TOKEN required}"
MODEL="${TAOS_MODEL:-kilo-auto/free}"

log "installing uv (idempotent)"
if ! command -v uv >/dev/null 2>&1 && [ ! -x /root/.local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="/root/.local/bin:$PATH"
echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc

if [ ! -d /root/.hermes/hermes-agent ]; then
    log "running Hermes installer (--skip-setup)"
    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup
fi

HERMES_BIN=""
for c in /root/.local/bin/hermes /root/.hermes/hermes-agent/.venv/bin/hermes /root/.hermes/hermes-agent/venv/bin/hermes; do
    [ -L "$c" -o -x "$c" ] && HERMES_BIN="$c" && break
done
[ -z "$HERMES_BIN" ] && HERMES_BIN=$(command -v hermes || true)
[ -z "$HERMES_BIN" ] && { log "ERROR: hermes binary not found"; exit 2; }
log "hermes at $HERMES_BIN"

log "writing /root/.hermes/.env (gateway env vars)"
mkdir -p /root/.hermes /root/.hermes/gateway
cat > /root/.hermes/.env <<ENVEOF
OPENAI_API_KEY=$LLM_KEY
OPENAI_BASE_URL=http://127.0.0.1:4000/v1
HERMES_INFERENCE_PROVIDER=custom
HERMES_DEFAULT_MODEL=$MODEL
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
ENVEOF
chmod 600 /root/.hermes/.env

log "patching /root/.hermes/config.yaml model.{provider,base_url,api_key}"
pip3 install --break-system-packages --quiet pyyaml httpx 2>&1 | tail -3 || true
# Read MODEL/LLM_KEY via os.environ so any shell-special characters in the
# values can't break the python literal or be interpreted as code. The
# unquoted heredoc would otherwise interpolate them as bash strings first.
TAOS_MODEL_ENV="$MODEL" TAOS_LLM_KEY_ENV="$LLM_KEY" python3 - <<'PYEOF'
import yaml, os
p = "/root/.hermes/config.yaml"
data = yaml.safe_load(open(p).read()) if os.path.exists(p) else {}
m = data.setdefault("model", {})
m["default"] = os.environ["TAOS_MODEL_ENV"]
m["provider"] = "custom"
m["base_url"] = "http://127.0.0.1:4000/v1"
m["api_key"] = os.environ["TAOS_LLM_KEY_ENV"]
with open(p, "w") as f: yaml.safe_dump(data, f, default_flow_style=False)
print("model patched OK")
PYEOF

log "creating systemd unit for Hermes gateway (foreground / run mode)"
cat > /etc/systemd/system/hermes-gateway.service <<UNIT
[Unit]
Description=Hermes Agent Gateway (foreground / run mode)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root
EnvironmentFile=/root/.hermes/.env
Environment=PATH=/root/.local/bin:/usr/bin:/bin
ExecStart=$HERMES_BIN gateway run
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/hermes-gateway.log
StandardError=append:/var/log/hermes-gateway.log

[Install]
WantedBy=multi-user.target
UNIT

log "writing taOS-Hermes bridge"
mkdir -p /opt/taos
cat > /opt/taos/taos-hermes-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-Hermes bridge: subscribes to taOS SSE for this agent, forwards
user messages to the local Hermes api_server (/v1/chat/completions) at
127.0.0.1:8642, and POSTs replies back to taOS via the openclaw reply
URL. Lets Hermes participate in chat through the existing
agent_chat_router → bridge_session pipeline that openclaw uses today —
no taOS-side changes required.

Env (injected by deployer): TAOS_BRIDGE_URL, TAOS_AGENT_NAME,
TAOS_LOCAL_TOKEN, LITELLM_API_KEY (optional, for hermes auth).

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [hermes-bridge] %(message)s")
log = logging.getLogger("hermes-bridge")

BRIDGE_URL = os.environ["TAOS_BRIDGE_URL"]
AGENT_NAME = os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN = os.environ["TAOS_LOCAL_TOKEN"]
HERMES_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
HERMES_KEY = os.environ.get("LITELLM_API_KEY", "")
HERMES_MODEL = os.environ.get("TAOS_MODEL", "kilo-auto/free")
RECONNECT_DELAY = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds — grows to 2, 4, 8 for retries 1-3
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
    f"You are {AGENT_NAME}, an autonomous agent running inside the Hermes "
    "Agent Gateway (NousResearch/hermes-agent) deployed on taOS. When asked "
    "what framework you run on, say Hermes. The underlying language model "
    "is routed through taOS's LiteLLM proxy and is an implementation detail "
    "— do not describe yourself as Claude/GPT/etc. just because the model "
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


async def call_hermes(client: httpx.AsyncClient, messages: list) -> str:
    """Call Hermes' OpenAI-compatible /v1/chat/completions and return the
    assistant's reply text. Retries with exponential backoff on transient
    failures; returns a short error string if all attempts fail so the
    user always sees something."""
    payload = {
        "model": HERMES_MODEL,
        "messages": messages,
    }
    headers = {"Content-Type": "application/json"}
    if HERMES_KEY:
        headers["Authorization"] = f"Bearer {HERMES_KEY}"
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(f"{HERMES_URL}/v1/chat/completions",
                                      json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # Non-200: 5xx are retryable, 4xx are not
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("hermes %d (attempt %d/%d) — retry in %.1fs",
                            resp.status_code, attempt, MAX_RETRIES, delay)
                last_error = f"[hermes returned {resp.status_code}: {resp.text[:200]}]"
                await asyncio.sleep(delay)
                continue
            return f"[hermes returned {resp.status_code}: {resp.text[:200]}]"
        except Exception as e:
            detail = str(e) or type(e).__name__
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("hermes error %s (attempt %d/%d) — retry in %.1fs",
                            detail, attempt, MAX_RETRIES, delay)
                last_error = f"[hermes error: {detail}]"
                await asyncio.sleep(delay)
                continue
            return f"[hermes error: {detail}]"
    return last_error or "[hermes error: unknown]"


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
        log.info("user_message id=%s already processed — skipping", msg_id)
        return False
    if msg_id:
        if len(_seen) >= _MAX_SEEN:
            # Discard oldest half to keep memory bounded
            _seen.clear()
        _seen.add(msg_id)

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
    messages = [{"role": "system", "content": system}]
    if ctx:
        messages.append({"role": "user", "content": f"Recent conversation:\n{ctx}"})
    messages.append({"role": "user", "content": text})
    if attach_line:
        messages.append({"role": "user", "content": attach_line})
    await _thinking(client, cid, "start")
    try:
        reply = await call_hermes(client, messages)
    finally:
        await _thinking(client, cid, "end")
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed NO_RESPONSE for id=%s", msg_id)
        return False

    # If the reply is an error, start the cooldown to prevent tight retry loops
    if final.startswith("[hermes "):
        log.warning("hermes error reply for id=%s — enabling %.1fs cooldown", msg_id, ERROR_COOLDOWN)
        _error_until[0] = asyncio.get_event_loop().time() + ERROR_COOLDOWN

    await post_reply(client, channel["reply_url"], channel["auth_bearer"],
                     msg_id, trace_id, final, cid)
    return True


async def sse_loop(client: httpx.AsyncClient, channel: dict, stop: asyncio.Event) -> None:
    seen_ids: set[str] = set()
    error_until: list[float] = [0.0]  # mutable so tasks can update it
    while not stop.is_set():
        try:
            log.info("SSE connecting to %s", channel["events_url"])
            async with client.stream("GET", channel["events_url"],
                                      headers={"Authorization": f"Bearer {channel['auth_bearer']}",
                                               "Accept": "text/event-stream",
                                               "Cache-Control": "no-cache"},
                                      timeout=None) as resp:
                if resp.status_code != 200:
                    log.warning("SSE %s — retry", resp.status_code)
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
                                asyncio.create_task(handle_user_message(
                                    client, evt, channel, seen_ids, error_until))
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


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient() as client:
        # Wait for Hermes api_server to be healthy
        for i in range(40):
            try:
                r = await client.get(f"{HERMES_URL}/health", timeout=5)
                if r.status_code == 200:
                    log.info("Hermes api_server healthy")
                    break
            except Exception:
                pass
            await asyncio.sleep(3)
        else:
            log.warning("Hermes api_server health never returned 200; continuing anyway")

        boot = await fetch_bootstrap(client)
        channel = boot["channel"]
        log.info("bootstrap OK: agent=%s session=%s", boot.get("agent_name"), boot.get("session_id"))
        await sse_loop(client, channel, stop)


if __name__ == "__main__":
    asyncio.run(main())
BRIDGE_EOF
chmod +x /opt/taos/taos-hermes-bridge.py

cat > /etc/systemd/system/taos-hermes-bridge.service <<UNIT
[Unit]
Description=taOS-Hermes bridge (SSE → Hermes /v1/chat/completions)
After=hermes-gateway.service network.target
Wants=hermes-gateway.service
[Service]
Type=simple
Environment=TAOS_BRIDGE_URL=$BRIDGE_URL
Environment=TAOS_AGENT_NAME=$AGENT_NAME
Environment=TAOS_LOCAL_TOKEN=$LOCAL_TOKEN
Environment=LITELLM_API_KEY=$LLM_KEY
Environment=TAOS_MODEL=$MODEL
Environment=HERMES_API_URL=http://127.0.0.1:8642
ExecStart=/usr/bin/python3 /opt/taos/taos-hermes-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-hermes-bridge.log
StandardError=append:/var/log/taos-hermes-bridge.log
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable hermes-gateway.service
systemctl start hermes-gateway.service

log "waiting for Hermes :8642 (up to 90s)"
for i in $(seq 1 30); do
    sleep 3
    if curl -fsS http://127.0.0.1:8642/health > /dev/null 2>&1; then
        log "Hermes api_server ready"
        break
    fi
done

systemctl enable --now taos-hermes-bridge.service
mkdir -p /opt/taos
echo "hermes-0.1" > /opt/taos/framework.version
log "done"
