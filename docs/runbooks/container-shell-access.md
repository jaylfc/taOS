# Container Shell Access

**Goal:** reach a running agent container's shell from the host, including
when the in-UI shell shortcut is unavailable.

taOS agents run inside isolated LXC (or Docker) containers. The primary way
to interact with a containerised agent is via the taOS desktop shell — the
in-UI terminal reachable from the Agents app. When that shortcut is
unavailable (network issue, browser disconnect, companion WS bug), you can
reach the container directly from the host.

## Don't use `incus console`

`incus console <container>` prompts for a **login and password**. These
credentials are never generated, documented, or exposed to end users by
taOS — the container images ship with password-less root. `incus console`
attaches to the container's virtual console (getty), which expects a login
that does not exist. This is not a bug in your setup; `incus console` is
the wrong tool for this.

## Use `incus exec`

`incus exec` runs a command directly inside the container, bypassing the
login prompt entirely. This is the supported host-side fallback:

```bash
# Interactive shell (bash in the agent container):
incus exec taos-agent-<slug> -- bash

# Run a single command:
incus exec taos-agent-<slug> -- ls -la /workspace

# Run a command with full environment:
incus exec taos-agent-<slug> -- env TERM=xterm-256color bash
```

### Container naming

Agent containers are named `taos-agent-<slug>` where `<slug>` is the lowercase
URL-safe version of the agent's display name with spaces replaced by
hyphens. You can find the exact container name with:

```bash
incus list --format csv --columns n | grep '^taos-agent-'
```

Or, from within taOS, open the Agents app — the container name is shown
in the agent detail panel.

## `taos agent exec` (preferred when available)

When the taOS CLI is installed and the taOS API is reachable, use the
built-in wrapper instead of raw `incus exec`:

```bash
taos agent exec my-agent -- bash
taos agent exec my-agent -- ls -la /workspace
taos agent exec my-agent -- journalctl --no-pager -n 50
```

The CLI wrapper handles container name resolution automatically (you use
the human-readable agent name, not the container slug) and works across
both LXC and Docker runtimes.

## Docker containers

If your taOS instance uses Docker instead of LXC (Settings → Container
Runtime), the equivalent commands are:

```bash
# List running agent containers:
docker ps --filter "name=taos-agent-"

# Interactive shell:
docker exec -it taos-agent-<slug> bash

# Single command:
docker exec -i taos-agent-<slug> ls -la /workspace
```

The principle is identical: `exec` bypasses the virtual console and runs
directly inside the container.

## Quick reference

| Goal | Command |
|---|---|
| Interactive shell (LXC) | `incus exec taos-agent-<slug> -- bash` |
| Single command (LXC) | `incus exec taos-agent-<slug> -- <cmd>` |
| Single command (Docker) | `docker exec -i taos-agent-<slug> <cmd>` |
| List agent containers (LXC) | `incus list --format csv --columns n \| grep '^taos-agent-'` |
| Interactive shell (Docker) | `docker exec -it taos-agent-<slug> bash` |
| Preferred CLI wrapper | `taos agent exec <name> -- bash` |

## Related

- [Container upgrade runbook](./container-upgrade.md) — full rebuild workflow
- [Framework swap runbook](./framework-swap.md) — changing an agent's framework
- [Discussion #357](https://github.com/jaylfc/tinyagentos/discussions/357) —
  original report that surfaced the `incus console` login gap


## Agent wall probe

The agent wall probe (`scripts/agent_wall_probe.py`) is a read-only diagnostic tool
that runs inside an agent container to verify wall/containment. It uses only the
Python standard library, prints one line per check in the format
`<STATUS> <check-id> <detail>`, and exits with code 0 (no FAIL/UNKNOWN), 1 (any FAIL),
or 2 (no FAIL but any UNKNOWN).

### Usage

```bash
python3 agent_wall_probe.py [--controller URL] [--target HOST:PORT ...] [--internet URL] [--json]
```

Defaults: `--controller` from env `TAOS_URL` (fallback `http://127.0.0.1:6969`),
`--internet` defaults to `https://example.com`. All addresses come from arguments/env
only; no real network addresses are embedded in the tool.

### Push into an agent container and run

Using `incus file push` (LXC):

```bash
incus file push scripts/agent_wall_probe.py taos-agent-<slug>/tmp/agent_wall_probe.py
incus exec taos-agent-<slug> -- python3 /tmp/agent_wall_probe.py
```

Using `docker cp`/`docker exec` (Docker):

```bash
docker cp scripts/agent_wall_probe.py taos-agent-<slug>:/tmp/agent_wall_probe.py
docker exec -i taos-agent-<slug> python3 /tmp/agent_wall_probe.py
```

### Status meanings

- `PASS` - wall holds
- `FAIL` - wall breached
- `UNKNOWN` - probe could not run (e.g. timeout on positive control or unreadable file); never counted as PASS
- `SKIP` - check not applicable yet

The positive control `internet-control` (HTTPS GET to `--internet`) gates `reach:`
checks: if internet control fails, every `reach:` line becomes `UNKNOWN` instead of `PASS`.
