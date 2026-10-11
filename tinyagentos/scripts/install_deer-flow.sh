#!/usr/bin/env bash
# Install DeerFlow inside an LXC agent container + the taOS-DeerFlow bridge.
# The DeerFlow provisioning mirrors app-catalog/agents/deer-flow/scripts/install.sh
# (deployer.py prefers this in-repo script over the catalog one, so it must do
# both). The bridge subscribes to taOS SSE and forwards chat to DeerFlow's
# LangGraph runs API (POST /api/runs/wait) on 127.0.0.1:8001.
# Idempotent: safe to re-run on an already-provisioned container.
set -euo pipefail

log() { echo "[$(date -u +%H:%M:%S)] deer-flow-install: $*"; }

AGENT_NAME="${TAOS_AGENT_NAME:?TAOS_AGENT_NAME required}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?TAOS_BRIDGE_URL required}"
LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?TAOS_LOCAL_TOKEN required}"

# ---------------------------------------------------------------------------
# 1. System dependencies
# ---------------------------------------------------------------------------
log "installing system dependencies"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  python3 python3-venv python3-pip git curl ca-certificates build-essential

# ---------------------------------------------------------------------------
# 2. Install uv (astral) if absent, and resolve its real path
# ---------------------------------------------------------------------------
# uv provisions Python 3.12 and the backend deps from backend/.python-version.
if ! command -v uv >/dev/null 2>&1 && [ ! -x /root/.local/bin/uv ]; then
  UV_VERSION="0.12.10"
  _fetch_and_verify() {
    local url="$1"
    local expected="$2"
    local dest="$3"
    if ! curl -fsSL -o "$dest" "$url"; then
      log "FATAL: failed to download $url"
      exit 1
    fi
    local actual
    actual=$(sha256sum "$dest" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
      log "FATAL: hash mismatch for $url (expected $expected, got $actual)"
      rm -f "$dest"
      exit 1
    fi
  }
  # Immutable, version-pinned installer (same pin as the catalog install.sh):
  #   url: https://github.com/astral-sh/uv/releases/download/0.12.10/uv-installer.sh
  #   sha256sum: a3196b75f697a1adaa5e4af34ffba7629c710931ab1dac33bab59ecf228080bb
  _fetch_and_verify "https://github.com/astral-sh/uv/releases/download/0.12.10/uv-installer.sh" \
    "a3196b75f697a1adaa5e4af34ffba7629c710931ab1dac33bab59ecf228080bb" \
    /tmp/uv-install.sh
  UV_VERSION="${UV_VERSION}" sh /tmp/uv-install.sh
fi
export PATH="$HOME/.local/bin:/root/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  log "FATAL: uv not found after install"
  exit 1
fi
UV_BIN="$(command -v uv)"
log "uv at $UV_BIN"

# ---------------------------------------------------------------------------
# 3. Clone DeerFlow into /opt/deer-flow and provision the backend
# ---------------------------------------------------------------------------
if [ -d /opt/deer-flow/.git ]; then
  log "repo already present, pulling latest"
  git -C /opt/deer-flow pull --ff-only
else
  git clone --depth 1 https://github.com/bytedance/deer-flow /opt/deer-flow
fi
(cd /opt/deer-flow/backend && "$UV_BIN" sync)

# ---------------------------------------------------------------------------
# 4. config.yaml pointing DeerFlow at the taOS LiteLLM proxy
# ---------------------------------------------------------------------------
# Literal $ENV_VAR tokens are expanded by DeerFlow at load time from the
# EnvironmentFile below. token_counting: char avoids DeerFlow's startup
# tiktoken cache network fetch. LocalSandboxProvider needs no Docker.
cat > /opt/deer-flow/config.yaml <<'CONFIG'
models:
  - name: taos-default
    use: langchain_openai:ChatOpenAI
    model: $TAOS_MODEL
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_BASE_URL
    request_timeout: 600.0

memory:
  token_counting: char

sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: false
CONFIG

# ---------------------------------------------------------------------------
# 5. Snapshot the deployer-injected env into an EnvironmentFile
# ---------------------------------------------------------------------------
# systemd units start from a clean environment, so capture the values here.
# DEER_FLOW_AUTH_DISABLED=1 turns off the gateway's login auth and CSRF guard
# (browser-only features). Safe: the gateway binds 127.0.0.1:8001 and only the
# bridge below reaches it. Without it POST /api/runs/wait returns 403.
cat > /opt/deer-flow/deer-flow.env <<ENVFILE
TAOS_MODEL=${TAOS_MODEL:-}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_BASE_URL=${OPENAI_BASE_URL:-}
DEER_FLOW_CONFIG_PATH=/opt/deer-flow/config.yaml
PYTHONPATH=.
DEER_FLOW_AUTH_DISABLED=1
ENVFILE
chmod 600 /opt/deer-flow/deer-flow.env

# ---------------------------------------------------------------------------
# 6. DeerFlow systemd unit
# ---------------------------------------------------------------------------
mkdir -p /var/log/deer-flow
cat > /etc/systemd/system/deer-flow.service <<UNIT
[Unit]
Description=DeerFlow SuperAgent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/deer-flow/backend
EnvironmentFile=/opt/deer-flow/deer-flow.env
ExecStart=${UV_BIN} run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/deer-flow/deer-flow.log
StandardError=append:/var/log/deer-flow/deer-flow.log

[Install]
WantedBy=multi-user.target
UNIT

# ---------------------------------------------------------------------------
# 7. taOS-DeerFlow bridge
# ---------------------------------------------------------------------------
log "installing bridge deps"
pip3 install --break-system-packages --quiet httpx 2>&1 | tail -3

log "writing taOS-DeerFlow bridge"
mkdir -p /opt/taos
cat > /opt/taos/taos-deer-flow-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-DeerFlow bridge: subscribes to taOS SSE for this agent, forwards
user messages to the local DeerFlow LangGraph runs API (POST /api/runs/wait)
at 127.0.0.1:8001, and POSTs replies back to taOS via the openclaw reply URL.

The runs-API call and reply extraction mirror
tinyagentos/adapters/deer_flow_adapter.py.

Env (injected by deployer): TAOS_BRIDGE_URL, TAOS_AGENT_NAME,
TAOS_LOCAL_TOKEN.

Stdlib + httpx only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [deer-flow-bridge] %(message)s")
log = logging.getLogger("deer-flow-bridge")

BRIDGE_URL = os.environ["TAOS_BRIDGE_URL"]
AGENT_NAME = os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN = os.environ["TAOS_LOCAL_TOKEN"]
DEER_FLOW_URL = os.environ.get("DEER_FLOW_URL", "http://127.0.0.1:8001")
RECONNECT_DELAY = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds -- 2, 4 between connection retries
ERROR_COOLDOWN = 5.0      # seconds after a final error before accepting new messages
# DeerFlow is a long-horizon multi-step agent: a run can take minutes.
RUN_TIMEOUT = 600

# Retry ONLY on connection-level failures (DeerFlow still starting, or a
# transport blip before the request reached it). Never on a read timeout or
# an HTTP error status: the run may already be executing, and re-firing it
# would re-run the whole job and its side effects.
_RETRY_ON = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def fetch_bootstrap(client: httpx.AsyncClient) -> dict:
    url = f"{BRIDGE_URL}/api/openclaw/bootstrap?agent={AGENT_NAME}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, timeout=30)
    resp.raise_for_status()
    boot = resp.json()
    if boot.get("schema_version") != 1:
        raise RuntimeError(f"unsupported bootstrap schema_version={boot.get('schema_version')}")
    return boot


_PREAMBLE = (
    f"You are {AGENT_NAME}, an autonomous agent running inside DeerFlow "
    "(bytedance/deer-flow) deployed on taOS. When asked what framework you "
    "run on, say DeerFlow. The underlying language model is routed through "
    "taOS's LiteLLM proxy and is an implementation detail, do not describe "
    "yourself as Claude/GPT/etc. just because of the model weights."
)


def _render_context(ctx):
    if not ctx:
        return ""
    lines = []
    for m in ctx:
        who = m.get("author_id") or m.get("role") or "?"
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


def _msg_content_text(msg) -> str:
    """Render one message's content as plain text.

    Handles {"content": str} and LangChain {"content": [{"type":"text",...}]}.
    """
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


_NO_REPLY = "[deer-flow error: no assistant message in response]"


def _extract_text(messages) -> str:
    """Pull the assistant reply from a list of LangGraph/LangChain messages.

    The final message is not necessarily the assistant turn (after a tool call
    it can be a tool message), so scan from the end for the last AI message.
    Returns a bridge error when the run produced no assistant text.
    """
    if not messages:
        return _NO_REPLY
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or msg.get("type")
        if role in ("assistant", "ai", "AIMessage"):
            text = _msg_content_text(msg)
            if text:
                return text
    # Never post a tool result or serialized tool call as the agent's reply.
    return _NO_REPLY


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


async def call_deer_flow(client: httpx.AsyncClient, text: str) -> str:
    """POST one user turn to DeerFlow's /api/runs/wait and return the
    assistant's reply text. Retries only connection-level failures; returns
    a short error string otherwise so the user always sees something."""
    payload = {
        "input": {"messages": [{"role": "user", "content": text}]},
        "context": {"model_name": "taos-default"},
    }
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(f"{DEER_FLOW_URL}/api/runs/wait",
                                      json=payload, timeout=RUN_TIMEOUT)
        except _RETRY_ON as e:
            last_error = f"[deer-flow error: {str(e) or type(e).__name__}]"
            if attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                log.warning("deer-flow unreachable %s (attempt %d/%d), retry in %.1fs",
                            last_error, attempt, MAX_RETRIES, delay)
                await asyncio.sleep(delay)
                continue
            return last_error
        except Exception as e:
            return f"[deer-flow error: {str(e) or type(e).__name__}]"
        if resp.status_code != 200:
            return f"[deer-flow returned {resp.status_code}: {resp.text[:200]}]"
        try:
            data = resp.json()
        except Exception as e:
            return f"[deer-flow error: bad JSON: {e}]"
        # The body is the final serialized channel-values. The messages list
        # may sit at the top level or under output/values.
        messages = (
            data.get("messages")
            or (data.get("output") or {}).get("messages")
            or (data.get("values") or {}).get("messages")
            or []
        )
        return _extract_text(messages)
    return last_error or "[deer-flow error: unknown]"


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

    # Dedup: never re-process the same message (bounded set).
    _MAX_SEEN = 1000
    if msg_id and msg_id in _seen:
        log.info("user_message id=%s already processed, skipping", msg_id)
        return False
    if msg_id:
        if len(_seen) >= _MAX_SEEN:
            _seen.clear()
        _seen.add(msg_id)

    now = asyncio.get_event_loop().time()
    if now < _error_until[0]:
        log.info("user_message id=%s suppressed during error cooldown (%.1fs remaining)",
                 msg_id, _error_until[0] - now)
        return False

    log.info("user_message id=%s text=%r force=%s", msg_id, text[:80], force)
    # DeerFlow's lead agent owns its system prompt, so taOS instructions ride
    # in the single user turn rather than as a second system message.
    parts = [_PREAMBLE + ("\n\nYou were directly addressed. Reply naturally; do not output NO_RESPONSE."
        if force else
        "\n\nIf you were not explicitly @mentioned and this message is not for you, reply with exactly: NO_RESPONSE\nOtherwise reply naturally. Keep it short in group chats.")]
    if ctx:
        parts.append(f"Recent conversation:\n{ctx}")
    parts.append(f"Message:\n{text}")
    if attach_line:
        parts.append(attach_line)
    prompt = "\n\n".join(parts)
    await _thinking(client, cid, "start")
    try:
        reply = await call_deer_flow(client, prompt)
    finally:
        await _thinking(client, cid, "end")
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed NO_RESPONSE for id=%s", msg_id)
        return False

    if final.startswith("[deer-flow "):
        log.warning("deer-flow error reply for id=%s, enabling %.1fs cooldown", msg_id, ERROR_COOLDOWN)
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
        # Wait for the DeerFlow gateway to answer (the LangGraph import takes
        # ~30-60s on ARM before uvicorn binds). Any HTTP response means up.
        for i in range(40):
            try:
                await client.get(f"{DEER_FLOW_URL}/", timeout=5)
                log.info("DeerFlow gateway reachable")
                break
            except Exception:
                pass
            await asyncio.sleep(3)
        else:
            log.warning("DeerFlow gateway never answered; continuing anyway")

        boot = await fetch_bootstrap(client)
        channel = boot["channel"]
        log.info("bootstrap OK: agent=%s session=%s", boot.get("agent_name"), boot.get("session_id"))
        await sse_loop(client, channel, stop)


if __name__ == "__main__":
    asyncio.run(main())
BRIDGE_EOF
chmod +x /opt/taos/taos-deer-flow-bridge.py

# Bridge env (carries TAOS_LOCAL_TOKEN) lives in a mode-600 file, not in the
# world-readable unit file.
cat > /opt/taos/deer-flow-bridge.env <<ENVFILE
TAOS_BRIDGE_URL=$BRIDGE_URL
TAOS_AGENT_NAME=$AGENT_NAME
TAOS_LOCAL_TOKEN=$LOCAL_TOKEN
DEER_FLOW_URL=http://127.0.0.1:8001
ENVFILE
chmod 600 /opt/taos/deer-flow-bridge.env

cat > /etc/systemd/system/taos-deer-flow-bridge.service <<UNIT
[Unit]
Description=taOS-DeerFlow bridge (SSE to DeerFlow /api/runs/wait)
After=deer-flow.service network.target
Wants=deer-flow.service
[Service]
Type=simple
EnvironmentFile=/opt/taos/deer-flow-bridge.env
ExecStart=/usr/bin/python3 /opt/taos/taos-deer-flow-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-deer-flow-bridge.log
StandardError=append:/var/log/taos-deer-flow-bridge.log
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
# restart (not start) so a re-run picks up the rewritten config, env and bridge.
systemctl enable deer-flow.service
systemctl restart deer-flow.service
systemctl enable taos-deer-flow-bridge.service
systemctl restart taos-deer-flow-bridge.service
echo "deer-flow-2.1.0" > /opt/taos/framework.version
log "done"
