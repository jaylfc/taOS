#!/bin/bash
# OpenCrabs framework install (runs inside the agent container at deploy).
#
# STATUS: DRAFT -- the binary download + bridge are modeled on the proven
# install_smolagents.sh, but the OpenCrabs provider config (config.toml /
# keys.toml) has NOT been verified against a live `opencrabs run`. See the
# VERIFY markers below. Do a live Fedora-worker deploy round-trip before
# relying on this. Notes: ~/.taos-team/opencrabs-deploy-notes.md.
#
# Mechanism: deployer.py auto-runs tinyagentos/scripts/install_<framework>.sh
# when present, so this only ever runs for framework==opencrabs (additive, no
# effect on other frameworks).
set -euo pipefail
log() { echo "[$(date -u +%H:%M:%S)] opencrabs-install: $*"; }

AGENT_NAME="${TAOS_AGENT_NAME:?}"; LLM_KEY="${LITELLM_API_KEY:?}"
BRIDGE_URL="${TAOS_BRIDGE_URL:?}"; LOCAL_TOKEN="${TAOS_LOCAL_TOKEN:?}"
MODEL="${TAOS_MODEL:-kilo-auto/free}"
# OpenCrabs talks to the taOS LiteLLM proxy as a custom OpenAI-compatible
# provider; OPENAI_BASE_URL is injected by the deployer (default LiteLLM /v1).
LLM_BASE="${OPENAI_BASE_URL:-http://127.0.0.1:4000/v1}"

# --- 1. Install the opencrabs binary (per-arch GitHub release) ----------------
OPENCRABS_VERSION="v0.3.55"   # pinned + reproducible; bump as releases land
_arch="$(uname -m)"
case "$_arch" in
  x86_64|amd64) _rel_arch="amd64" ;;
  aarch64|arm64) _rel_arch="arm64" ;;
  *) log "unsupported arch $_arch"; exit 1 ;;
esac
_tarball="opencrabs-${OPENCRABS_VERSION}-linux-${_rel_arch}.tar.gz"
_url="https://github.com/adolfousier/opencrabs/releases/download/${OPENCRABS_VERSION}/${_tarball}"
log "download $_tarball"
_tmp="$(mktemp -d)"
curl -fsSL "$_url" -o "$_tmp/oc.tar.gz"
tar -xzf "$_tmp/oc.tar.gz" -C "$_tmp"
# the tarball ships a bare `opencrabs` binary
install -m 0755 "$(find "$_tmp" -type f -name opencrabs | head -1)" /usr/local/bin/opencrabs
rm -rf "$_tmp"
/usr/local/bin/opencrabs --version || { log "opencrabs binary did not run"; exit 1; }

# --- 2. Provider config pointing OpenCrabs at the taOS LiteLLM proxy -----------
# VERIFY (live): confirm against a real `opencrabs run` that
#   (a) the active provider is selected via `providers = ["custom:litellm"]`,
#   (b) base_url should be the chat-completions path vs bare /v1,
#   (c) opencrabs does not require `opencrabs init`/onboarding before `run`.
# See ~/.taos-team/opencrabs-deploy-notes.md for the schema research.
mkdir -p /root/.opencrabs
cat > /root/.opencrabs/config.toml <<CFG
# taOS-managed OpenCrabs config. Routes through the taOS LiteLLM proxy.
providers = ["custom:litellm"]

[providers.custom.litellm]
enabled = true
base_url = "${LLM_BASE}/chat/completions"
default_model = "${MODEL}"
models = ["${MODEL}"]
CFG
cat > /root/.opencrabs/keys.toml <<KEYS
[providers.custom.litellm]
api_key = "${LLM_KEY}"
KEYS
chmod 600 /root/.opencrabs/keys.toml

# --- 3. taOS <-> OpenCrabs bridge (SSE consumer; drives `opencrabs run`) -------
# Bridge machinery (bootstrap / SSE / reply / thinking) is the same proven shape
# as install_smolagents.sh; only _run differs (shell out to the opencrabs CLI).
mkdir -p /opt/taos
cat > /opt/taos/taos-opencrabs-bridge.py <<'BRIDGE_EOF'
#!/usr/bin/env python3
"""taOS-opencrabs bridge: feeds bus messages to `opencrabs run`."""
from __future__ import annotations
import asyncio, json, logging, os, signal, subprocess
from concurrent.futures import ThreadPoolExecutor
import httpx
logging.basicConfig(level=logging.INFO, format="%(asctime)s [oc-bridge] %(message)s")
log = logging.getLogger("oc-bridge")
BRIDGE_URL=os.environ["TAOS_BRIDGE_URL"]; AGENT_NAME=os.environ["TAOS_AGENT_NAME"]
LOCAL_TOKEN=os.environ["TAOS_LOCAL_TOKEN"]
_pool=ThreadPoolExecutor(max_workers=2)
_PREAMBLE = (
    f"You are the agent named {AGENT_NAME}, running inside the OpenCrabs "
    "framework on taOS. If asked what framework you run on, answer OpenCrabs. "
    "The model routing is an implementation detail of taOS's proxy.\n\n"
)
_REPLY_KEYS = ("response", "content", "text", "output", "message", "result")

def _extract(stdout: str) -> str:
    stdout = (stdout or "").strip()
    if not stdout:
        return ""
    data = None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line); break
                except json.JSONDecodeError:
                    continue
    if isinstance(data, dict):
        for k in _REPLY_KEYS:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        nested = data.get("message")
        if isinstance(nested, dict):
            v = nested.get("content") or nested.get("text")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return stdout

def _render_context(ctx):
    if not ctx:
        return ""
    return "\n".join(f"{m.get('author_id') or '?'}: {m.get('content','')}" for m in ctx)

def _suppress(reply, force):
    if force:
        return reply
    stripped = (reply or "").strip().lower().strip(".!,;:")
    return None if stripped == "no_response" else reply

def _run(text, force):
    rule = (" You were directly addressed, reply normally."
            if force else
            " If this message is not for you, reply with exactly NO_RESPONSE.")
    prompt = _PREAMBLE + rule + "\n\nTask: " + text
    try:
        proc = subprocess.run(
            ["opencrabs", "run", prompt, "--format", "json", "--auto-approve"],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return f"[opencrabs error: {(proc.stderr or proc.stdout).strip()[:300]}]"
        return _extract(proc.stdout)
    except Exception as e:
        return f"[opencrabs error: {e}]"

async def _thinking(c, ch_id, state):
    if not ch_id:
        return
    try:
        await c.post(f"{BRIDGE_URL}/api/chat/channels/{ch_id}/thinking",
                     json={"slug": AGENT_NAME, "state": state},
                     headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, timeout=5)
    except Exception:
        pass

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
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
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
chmod +x /opt/taos/taos-opencrabs-bridge.py
# httpx is needed by the bridge (the opencrabs binary itself is self-contained).
pip3 install --break-system-packages --quiet httpx 2>&1 | tail -2 || true

cat > /etc/systemd/system/taos-opencrabs-bridge.service <<UNIT
[Unit]
Description=taOS-opencrabs bridge
After=network.target
[Service]
Type=simple
Environment=TAOS_BRIDGE_URL=${BRIDGE_URL}
Environment=TAOS_AGENT_NAME=${AGENT_NAME}
Environment=TAOS_LOCAL_TOKEN=${LOCAL_TOKEN}
ExecStart=/usr/bin/python3 /opt/taos/taos-opencrabs-bridge.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/taos-opencrabs-bridge.log
StandardError=append:/var/log/taos-opencrabs-bridge.log
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now taos-opencrabs-bridge.service
echo "opencrabs-${OPENCRABS_VERSION}" > /opt/taos/framework.version
log "done"
