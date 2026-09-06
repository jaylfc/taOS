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
async def post_reply(c, u, t, mid, tid, txt, cid=None):
    body = {"kind": "final", "id": mid, "trace_id": tid, "content": txt}
    if cid: body["channel_id"] = cid
    try: await c.post(u, json=body, headers={"Authorization": f"Bearer {t}"}, timeout=30)
    except Exception as e: log.warning("reply: %s", e)
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
    while not stop.is_set():
        try:
            async with c.stream("GET", ch["events_url"],
                                 headers={"Authorization": f"Bearer {ch['auth_bearer']}",
                                          "Accept": "text/event-stream"},
                                 timeout=None) as r:
                if r.status_code != 200: await asyncio.sleep(2); continue
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
        except Exception as e: log.warning("sse: %s", e)
        if not stop.is_set(): await asyncio.sleep(2)
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
