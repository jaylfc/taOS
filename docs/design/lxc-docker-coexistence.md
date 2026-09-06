# LXC / Docker coexistence on a taOS host

**Status:** Active policy. LXC is the preferred agent runtime; Docker
coexists for the app store's containerised services but never
handicaps LXC.

## Why the policy

LXC gives taOS full-OS containers that match the host-owns-state model:
the container holds only code and the runtime; all state (memory, workspace,
secrets, trace data) lives on the host. That constraint makes framework
swaps, container upgrades, and cluster dispatch cheap. See
`docs/design/framework-agnostic-runtime.md` for the full argument.

Docker supports taOS's app-store services — things like Stripe webhooks,
gateway adapters, or community-published services — that ship as Docker
images and expect Docker networking. Refusing to run them would make the
app store impractical, so Docker is a first-class install option.

Both runtimes share the host kernel's netfilter stack. Docker installs
a `FORWARD DROP` policy and a `DOCKER-USER` chain at install time;
incus creates its own bridge and expects `FORWARD ACCEPT` for traffic
originating from that bridge. If nothing corrects for this, every taOS
agent container silently loses outbound internet. taOS accounts for that
clash at install time rather than at first-agent-deploy time.

## Clash matrix

| Clash class | Example | Fix |
|---|---|---|
| iptables FORWARD | Docker sets `FORWARD=DROP` at install, blocking incus bridge traffic | systemd oneshot inserts `ACCEPT` rules into `DOCKER-USER` for `incusbr0` |
| Bridge subnet | User's Docker has a bridge in 10.26.37.0/24 before incus install | `scripts/incus-bridge-probe.sh` detects and reassigns `incusbr0` to a free /24 |
| Chain re-ordering | Docker restarts and re-creates its chains | rules live in `DOCKER-USER`, which Docker does not touch on restart; `.path` unit re-fires oneshot on Docker PID appearance |
| First-boot race | Agent deploys before firewall rules land | unit is ordered `Before=tinyagentos.service` |
| Host port | Docker maps a port incus wants | LiteLLM binds `127.0.0.1` only; proxy devices are per-container, never host-wide — no collision possible |
| Runtime install after taOS | User runs `apt install docker-ce` later | `.path` unit fires the oneshot when `/var/run/docker.pid` appears |
| cgroups / memory | Both runtimes want to over-commit memory | no action needed; Linux CFS scheduler and cgroup v2 handle it |

## Install scenarios — what happens, what you would see

### Fresh Debian, no Docker ever

Debian's default `FORWARD` policy is `ACCEPT`. The `DOCKER-USER` chain
does not exist, so `host-firewall-up.sh` detects its absence and exits
without touching iptables. Incus works natively. No firewall service
intervention required at all; the unit is a verified no-op in this case.

### Docker present before taOS install

`install.sh` runs `scripts/incus-bridge-probe.sh` to check whether
Docker has already claimed the 10.26.37.0/24 range. If there is a
collision, the probe picks an unused /24 and writes a custom
`incusbr0` address into the incus preseed before `incus init`. Then
`install.sh` enables `tinyagentos-host-firewall.service`; on first
boot the oneshot inserts the `ACCEPT` rules into `DOCKER-USER`.
At the end of install, `install.sh` runs a smoke test: it launches a
throwaway incus container and runs `curl -sI https://github.com` from
inside it, then destroys the container. A non-200 response or a timeout
aborts the install with an actionable error.

### taOS installed, user adds Docker later

When the user runs `apt install docker-ce`, Docker's post-install
script starts `docker.service`, which writes `/var/run/docker.pid`.
The `.path` unit watching that path fires the oneshot immediately.
The oneshot inserts the `ACCEPT` rules into the freshly-created
`DOCKER-USER` chain. The five-minute `.timer` unit provides a
belt-and-suspenders catch for any edge case where the `.path` watcher
doesn't fire (e.g. the user installs Docker via a snap or a non-standard
package).

### Docker restart or upgrade

Docker reinstalls its own chains (`DOCKER`, `DOCKER-ISOLATION-STAGE-1`,
etc.) on restart but leaves `DOCKER-USER` alone by documented
convention. Our rules are inserted into `DOCKER-USER` and survive the
restart unchanged. The `.timer` runs every five minutes and
re-verifies idempotently regardless.

## Runtime selection policy

`tinyagentos/containers/backend.py::detect_runtime()` prefers LXC over
Docker when both are available. The function logs its selection decision
at INFO level so any "why did it pick X" question is answered by:

```
journalctl -u tinyagentos.service | grep detect_runtime
```

There is deliberately no config knob to flip the preference. An agent
runs on LXC if LXC is available. The Docker code path exists for
platforms that don't ship incus (some macOS installs, older Ubuntu
versions) but is a fallback, not a peer. Making it user-switchable
would create a support surface where "I turned off LXC" masks real
networking problems.

**Docker and the snapshot archive model.** Agent archive and restore rely
on `incus snapshot create` / `incus snapshot restore`. Docker has no
equivalent primitive: `docker commit` produces a new image from a running
container's filesystem but does not capture named, restorable snapshots in
the same atomic sense. On hosts where the Docker backend is active,
`snapshot_create` and `snapshot_restore` return a graceful "not supported"
response and the archive call falls back to a dir-copy path. This is
documented in `tinyagentos/containers/docker.py::DockerBackend.snapshot_create`.
The LXC backend is the only path that delivers the full snapshot-archive
guarantee described in `docs/design/architecture-pivot-v2.md`.

## Operational runbook

### Symptom: "Agent has no internet"

1. Confirm the agent container exists and is running:
   ```
   incus list
   ```
2. Test connectivity from inside the container:
   ```
   incus exec taos-agent-<name> -- curl -sI https://github.com
   ```
3. If that times out, check whether our rules are present:
   ```
   sudo iptables -L DOCKER-USER -v -n
   ```
   You should see `ACCEPT` rules referencing `incusbr0` near the top.
4. If the rules are missing, restart the firewall unit:
   ```
   sudo systemctl restart tinyagentos-host-firewall.service
   ```
5. If Docker is not installed on this host, `DOCKER-USER` will not
   exist and that is expected. Check the default FORWARD policy instead:
   ```
   sudo iptables -L FORWARD -v -n
   ```
   The policy line should read `ACCEPT`. If it reads `DROP`, something
   else on the host manipulated iptables; investigate with
   `sudo iptables-save | grep -i forward`.

### Adding Docker to a running taOS host

The `.path` unit should catch it automatically. To verify:

```
sudo systemctl status tinyagentos-host-firewall.service
journalctl -u tinyagentos-host-firewall.service | grep "inserted rule"
```

If the journal shows no "inserted rule" line within a minute of Docker
starting, run the restart manually:

```
sudo systemctl restart tinyagentos-host-firewall.service
```

### Removing Docker from a taOS host

```
sudo apt purge docker-ce docker.io containerd runc
```

On next run the oneshot detects that `DOCKER-USER` is absent and
exits cleanly. You may want to reset the FORWARD policy explicitly if
something else had set it to DROP before Docker arrived:

```
sudo iptables -P FORWARD ACCEPT
```

### Smoke-testing coexistence

This is the same check `install.sh` runs at the end of install. Run it
by hand at any time to re-verify the setup:

```
incus launch images:debian/12 taos-coexist-test --ephemeral
incus exec taos-coexist-test -- curl -sI https://github.com
incus stop taos-coexist-test
```

A 301 or 200 response from the `curl` confirms the LXC bridge can reach
the internet through Docker's iptables rules. A connection timeout
means the `DOCKER-USER` rules are missing; see the runbook above.

## Related

- `docs/design/framework-agnostic-runtime.md` — the rule that containers
  hold code and hosts hold state; the "Host firewall" subsection there
  covers the iptables mechanics in brief
- `scripts/host-firewall-up.sh` / `host-firewall-down.sh` — idempotent
  ACCEPT insertion and removal for `incusbr0` in `DOCKER-USER`
- `scripts/incus-bridge-probe.sh` — subnet collision probe run at install
  time when Docker is already present
- `systemd/tinyagentos-host-firewall.service` — `Type=oneshot` unit
  ordered after both Docker and incus, before `tinyagentos.service`
- `systemd/tinyagentos-host-firewall.path` — fires the oneshot when
  `/var/run/docker.pid` appears (Docker install or restart)
- `systemd/tinyagentos-host-firewall.timer` — five-minute belt-and-
  suspenders re-check
