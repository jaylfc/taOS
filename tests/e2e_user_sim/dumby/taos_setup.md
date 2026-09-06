# taOS connection details for the user-sim

## Endpoint

The Pi instance lives at the URL stored in `~/.taos-sim.env` on the
controller machine (this Mac). The file has mode `0600` and is **never**
committed.

```
TAOS_URL=http://<lan-ip>:<port>
TAOS_USER=<single-user-username>
TAOS_PASS=<single-user-password>
```

Subagent guidance: read these with shell expansion (`source ~/.taos-sim.env`)
or `grep -E '^TAOS_' ~/.taos-sim.env`. Do not echo the values into transcripts
or screenshots — when filling the password field, type via Playwright and
mask the value in any log line.

## Auth flow

1. Drive the browser to `${TAOS_URL}/auth/login`.
2. Fill the username + password fields, tick "Stay logged in", submit.
3. The session cookie is set automatically. All subsequent navigation works.

A REST shortcut is available for bulk read operations (no UI clicks):

```bash
curl -sS -c ${RUN_DIR}/.cookies -X POST "${TAOS_URL}/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"'"${TAOS_USER}"'","password":"'"${TAOS_PASS}"'","auto_login":true}'

curl -sS -b ${RUN_DIR}/.cookies "${TAOS_URL}/api/agents"
```

Use REST for quick read-back checks (e.g., "did the project really get
created?"). Use the browser for everything that affects the user
experience — that's the whole point of the test.

## Reachability

The Pi is on the LAN. If the URL fails to load:

1. Confirm `curl -sS ${TAOS_URL}/api/health` returns `{"status":"ok",…}`.
2. If unreachable, save a `BLOCKED` status with a precise summary
   (`pi_unreachable`) and stop. The controller will investigate.

## What lives on the Pi already

The Pi runs in single-user mode for `jay`. There may already be unrelated
agents from other tests; the user-sim must scope all its work to a fresh
project named **"Dumby the Dumbo"**. Do not modify or delete agents that
live outside this project.
