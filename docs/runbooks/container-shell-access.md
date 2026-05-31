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
docker exec taos-agent-<slug> ls -la /workspace
```

The principle is identical: `exec` bypasses the virtual console and runs
directly inside the container.

## Quick reference

| Goal | Command |
|---|---|
| Interactive shell (LXC) | `incus exec taos-agent-<slug> -- bash` |
| Single command (LXC) | `incus exec taos-agent-<slug> -- <cmd>` |
| List agent containers (LXC) | `incus list \| grep taos-agent-` |
| Interactive shell (Docker) | `docker exec -it taos-agent-<slug> bash` |
| Preferred CLI wrapper | `taos agent exec <name> -- bash` |

## Related

- [Container upgrade runbook](./container-upgrade.md) — full rebuild workflow
- [Framework swap runbook](./framework-swap.md) — changing an agent's framework
- [Discussion #357](https://github.com/jaylfc/tinyagentos/discussions/357) —
  original report that surfaced the `incus console` login gap
