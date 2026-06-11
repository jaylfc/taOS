# Runbook: Controller Rescue and Component Restarts

**Audience:** taOS admins, power users, and the taOS host agent diagnosing an instance that is down, hung, or misbehaving. If the desktop UI is reachable, prefer it: most recovery actions here have UI surfaces. This runbook is for when it is not.

**Where things live:** the install directory is `/opt/tinyagentos` on system installs (legacy installs used `~/tinyagentos`; substitute accordingly). The data directory is `<install dir>/data`. All commands below assume a Linux system install running as the `taos` user; see the end for user-mode and macOS variants.

---

## Quick triage

Work down this list. Stop at the first failing step: that layer is your problem.

| # | Check | Command | Healthy looks like |
|---|---|---|---|
| 1 | Host reachable | `ping <host>` | replies |
| 2 | Service state | `systemctl status tinyagentos` | `active (running)` |
| 3 | Port actually listening | `ss -tlnp \| grep 6969` | a `python` process on `0.0.0.0:6969` |
| 4 | API answering | `curl -s http://127.0.0.1:6969/api/health` | HTTP 200 |
| 5 | Version | `curl -s http://127.0.0.1:6969/api/version` | current release |

**Beware the half-alive state:** `active (running)` with port 6970 listening but **not** 6969 means the main app crashed during startup while the browser-proxy origin survived (issue #756). systemd will not restart it. Read the journal (below), fix the cause, then `systemctl restart tinyagentos`.

---

## Reading the journal

```bash
journalctl -u tinyagentos -n 100 --no-pager        # last 100 lines
journalctl -u tinyagentos --since "10 minutes ago" # around a restart
journalctl -xeu tinyagentos                        # why the last start failed
```

The line that matters most after a failed boot is the last Python traceback before `ERROR: Application startup failed. Exiting.`

---

## Complete restart

```bash
sudo systemctl restart tinyagentos
```

This is the big hammer: it also restarts LiteLLM (a child process of the controller) and re-runs the desktop bundle check. A clean boot takes roughly a minute on SBC hardware. Verify with triage steps 3 to 5.

Stop and start separately when you need a gap (for example to move the data dir):

```bash
sudo systemctl stop tinyagentos    # graceful stop, waits for in-flight work
sudo systemctl start tinyagentos
```

---

## Per-component restarts

| Component | What it is | Restart |
|---|---|---|
| Controller | `tinyagentos.service`, the main app on :6969 (+ browser proxy on :6970) | `sudo systemctl restart tinyagentos` |
| LiteLLM | model router on `127.0.0.1:4000`, child process of the controller, config under `/tmp/taos-litellm/` | restart the controller (it respawns LiteLLM) |
| qmd | shared embed/rerank provider, `qmd.service` on :7832 | `sudo systemctl restart qmd` |
| Agent containers | one LXC per agent, named `taos-agent-<name>` | `incus restart taos-agent-<name>` (or start/stop) |
| Postgres | LiteLLM's database | `sudo systemctl restart postgresql` |
| Store apps (Docker) | one container per installed app | `docker restart <name>` (`docker ps` to list) |
| RK3588 extras | `rkllama.service` (NPU models), `taos-rk3588-perf.service` (boot-time governors) | `sudo systemctl restart rkllama` |
| Workers | separate boxes, paired via the cluster | see `worker-lxc-enrollment.md` |

Notes:

- Stopped agent containers are often normal: agents that were archived or never started stay `STOPPED`. Check the Agents app or `incus list` against what you expect to be running.
- Restarting the controller does not touch agent containers. Agents reconnect on their own.

---

## Diagnosis toolkit

```bash
# What is listening where
ss -tlnp | grep -E "6969|6970|4000|7832"

# Is the process hung rather than dead? Dump live Python stacks
sudo /opt/tinyagentos/.venv/bin/pip install py-spy   # once
sudo /opt/tinyagentos/.venv/bin/py-spy dump --pid $(systemctl show -p MainPID --value tinyagentos)

# SQLite store integrity (controller must be stopped for a write check)
sqlite3 /opt/tinyagentos/data/<store>.db "PRAGMA integrity_check;"

# What version is installed vs latest
git -C /opt/tinyagentos log --oneline -1
git -C /opt/tinyagentos fetch origin && git -C /opt/tinyagentos log --oneline origin/master -1

# Disk space (a full disk breaks SQLite writes in odd ways)
df -h /opt /var
```

---

## Known failure signatures

| Symptom in journal / status | Cause | Fix |
|---|---|---|
| `status=200/CHDIR`, restart counter climbing | service user cannot traverse into the install dir (install under `/root`, or privileged setup skipped on a non-sudo install run) | re-run the installer with sudo; it repairs ownership and parent-dir permissions (#723, #724, #753) |
| `sqlite3.OperationalError: no such column: user_id` then `Application startup failed` | knowledge.db predates the multi-user schema and the migration never applied | update to a release with the guarded migration (#755); it self-heals on boot. Manual bridge: `sqlite3 data/knowledge.db "ALTER TABLE knowledge_items ADD COLUMN user_id TEXT NOT NULL DEFAULT '';"` |
| unit `active (running)`, :6970 listening, :6969 dead, journal shows a startup traceback | main server failed startup, proxy kept the process alive (#756) | fix the traceback's cause, then `systemctl restart tinyagentos` |
| `status=217/USER` | the `taos` system user does not exist (installer ran without privileges) | re-run the installer with sudo (#753) |
| Port already in use on 6969/6970 | a previous half-dead process still holds the port | `sudo systemctl stop tinyagentos`, confirm with `ss -tlnp`, `kill` any leftover, start again |
| UI loads but models error | LiteLLM child or postgres down | triage `ss \| grep 4000`, `systemctl status postgresql`, restart the controller |

---

## Updating to latest (and as a repair)

The installer is idempotent and doubles as a repair tool: it fixes ownership, permissions, units, and dependencies without touching your data.

```bash
curl -fsSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh | sudo bash
```

Manual update of an existing install:

```bash
cd /opt/tinyagentos
sudo -u taos git -c safe.directory=/opt/tinyagentos pull --ff-only origin master
sudo -u taos .venv/bin/pip install -q -e .
sudo systemctl restart tinyagentos
```

The `safe.directory` flag is needed because the repo is owned by the `taos` service user, not the account you are typing from.

---

## User-mode and macOS variants

- **User-mode systemd** (installer ran without root): same commands with `systemctl --user` and `journalctl --user -u tinyagentos`. No `taos` user exists; everything runs as you.
- **macOS** (launchd agent): `launchctl kickstart -k gui/$(id -u)/com.tinyagentos.controller` to restart; logs land in the paths configured in `~/Library/LaunchAgents/com.tinyagentos.controller.plist`.
