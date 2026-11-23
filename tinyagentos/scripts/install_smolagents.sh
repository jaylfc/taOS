#!/bin/bash
set -euo pipefail
log() { echo "[$(date -u +%H:%M:%S)] smolagents-install: $*"; }
AGENT_NAME="${TAOS_AGENT_NAME:?}"; LLM_KEY="${LITELLM_API_KEY:?}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?}"; LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?}"
MODEL="${TAOS_MODEL:-kilo-auto/free}"
log "pip install smolagents + deps"
pip3 install --break-system-packages --quiet smolagents httpx openai 2>&1 | tail -3
mkdir -p /opt/taos
cat > /opt/taos/taos-smolagents-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-smolagents bridge."""
from __future__ import annotations
import asyncio, json, logging, os, signal
from concurrent.futures import ThreadPoolExecutor
import httpx
logging.basicConfig(level=logging.INFO, format="%(asctime)s [smol-bridge] %(message)s")
log = logging.getLogger("smol-bridge")
BRIDGE_URL=os.environ["TAOS_BRIDGE_URL"]; AGENT_NAME=os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN=os.environ["TAOS_LOCAL_TOKEN"]; MODEL=os.environ.get("TAOS_MODEL","kilo-auto/free")
MAX_RETRIES = 3
RECONNECT_DELAY = 2.0
# Final reply retries: capped backoff (1, 2, 4, 8, 15 s then 15 s repeating)
# inside a total window of at least 180 s so a reply survives a controller
# restart (TimeoutStartSec=180 in scripts/systemd/tinyagentos.service).
REPLY_BACKOFF_STEPS = (1.0, 2.0, 4.0, 8.0, 15.0)
REPLY_WINDOW_S = 180.0
_pool=ThreadPoolExecutor(max_workers=2); _agent=None
_FRAMEWORK_PREAMBLE = (
    f"You are the agent named {AGENT_NAME}, running inside the SmolAgents "
    "framework (HuggingFace) on taOS. If asked what framework you run on, "
    "answer SmolAgents. The model weights routing is an implementation "
    "detail of taOS's proxy — don't claim to be Claude/GPT/etc.\n\nTask: "
)
def _build():
    global _agent
    if _agent: return _agent
    from smolagents import CodeAgent, OpenAIServerModel
    model = OpenAIServerModel(
        model_id=MODEL,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    _agent = CodeAgent(tools=[], model=model)
    return _agent
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

def _run(text, force):
    try:
        agent = _build()
        rule = (" You were directly addressed, reply normally."
                if force else
                " If this message isn't for you, reply with exactly NO_RESPONSE.")
        return str(agent.run(_FRAMEWORK_PREAMBLE + rule + "\n\nTask: " + text))
    except Exception as e:
        return f"[smolagents error: {e}]"
async def fetch_boot(c):
    r = await c.get(f"{BRIDGE_URL}/api/openclaw/bootstrap?agent={AGENT_NAME}",
                     headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, timeout=30)
    r.raise_for_status(); return r.json()
async def post_reply(client: httpx.AsyncClient, reply_url: str, token: str,
                     msg_id: str, trace_id: str, content: str, cid=None) -> bool:
    body = {"kind": "final", "id": msg_id, "trace_id": trace_id, "content": content}
    if cid: body["channel_id"] = cid
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    step = 0
    elapsed = 0.0  # time spent sleeping between retries (simulated time when sleep is patched)
    while True:
        try:
            resp = await client.post(reply_url, json=body, headers=headers, timeout=30)
            if 200 <= resp.status_code < 300:
                return True
            # 429 and 5xx are transient: retry inside the window so a reply
            # survives a controller restart. Other 4xx are rejected and not
            # reposted; the reply keeps its id and the controller's duplicate
            # guard (_seen_final_ids) makes a safe retry unnecessary.
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if elapsed < REPLY_WINDOW_S:
                    delay = REPLY_BACKOFF_STEPS[min(step, len(REPLY_BACKOFF_STEPS) - 1)]
                    step += 1
                    log.warning("reply POST %s, retry in %.1fs",
                                resp.status_code, delay)
                    await asyncio.sleep(delay)
                    elapsed += delay
                    continue
                log.warning("reply POST %s: giving up after %.0fs window",
                            resp.status_code, REPLY_WINDOW_S)
                return False
            log.warning("reply POST %s: %s", resp.status_code, resp.text[:300])
            return False
        except httpx.TransportError as e:
            if elapsed < REPLY_WINDOW_S:
                delay = REPLY_BACKOFF_STEPS[min(step, len(REPLY_BACKOFF_STEPS) - 1)]
                step += 1
                log.warning("reply POST error %s, retry in %.1fs", e, delay)
                await asyncio.sleep(delay)
                elapsed += delay
                continue
            log.warning("reply POST failed: %s", e)
            return False
        except Exception as e:
            log.warning("reply POST failed: %s", e)
            return False
async def handle(c, evt, ch):
    mid=evt.get("id",""); tid=evt.get("trace_id",mid); text=evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    attach_line = _render_attachments(evt.get("attachments") or [])
    base = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    full = f"{base}\n{attach_line}" if attach_line else base
    cid = evt.get("channel_id")
    log.info("user_message id=%s text=%r force=%s", mid, text[:80], force)
    await _thinking(c, cid, "start")
    try:
        reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    finally:
        await _thinking(c, cid, "end")
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed id=%s", mid); return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, cid)
async def sse(c, ch, stop):
    last_event_id = ""
    while not stop.is_set():
        try:
            headers = {"Authorization": f"Bearer {ch['auth_bearer']}",
                       "Accept": "text/event-stream"}
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            async with c.stream("GET", ch["events_url"],
                                 headers=headers,
                                 timeout=None) as r:
                if r.status_code != 200: await asyncio.sleep(RECONNECT_DELAY); continue
                t,d = "",""
                async for raw in r.aiter_lines():
                    if stop.is_set(): break
                    if raw == "":
                        if t=="user_message" and d:
                            try: asyncio.create_task(handle(c, json.loads(d), ch))
                            except Exception as e: log.warning("parse: %s", e)
                        t,d = "",""; continue
                    if raw.startswith(":"): continue
                    if raw.startswith("event:"): t = raw[6:].strip()
                    elif raw.startswith("data:"): d = raw[5:].lstrip()
                    elif raw.startswith("id:"): last_event_id = raw[3:].strip()
        except Exception as e: log.warning("sse: %s", e)
        if not stop.is_set(): await asyncio.sleep(RECONNECT_DELAY)
async def main():
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(s, stop.set)
    async with httpx.AsyncClient() as c:
        boot = await fetch_boot(c); log.info("bootstrap OK agent=%s", boot.get("agent_name"))
        await sse(c, boot["channel"], stop)
asyncio.run(main())
BRIDGE_EOF
chmod +x /opt/taos/taos-smolagents-bridge.py
cat > /etc/systemd/system/taos-smolagents-bridge.service <<UNIT
[Unit]
Description=taOS-smolagents bridge
After=network.target
[Service]
Type=simple
Environment=TAOS_BRIDGE_URL=${BRIDGE_URL}
Environment=TAOS_AGENT_NAME=${AGENT_NAME}
Environment=TAOS_LOCAL_TOKEN=${LOCAL_TOKEN}
Environment=LITELLM_API_KEY=${LLM_KEY}
Environment=TAOS_MODEL=${MODEL}
Environment=OPENAI_API_KEY=${LLM_KEY}
Environment=OPENAI_BASE_URL=http://127.0.0.1:4000/v1
ExecStart=/usr/bin/python3 /opt/taos/taos-smolagents-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-smolagents-bridge.log
StandardError=append:/var/log/taos-smolagents-bridge.log
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now taos-smolagents-bridge.service
echo "smolagents-1.x" > /opt/taos/framework.version
log "done"
