# Container Upgrade

**Goal:** rebuild every agent container from a fresh image (security patch,
new base OS, updated framework pin) with zero user-visible state loss.

The [framework-agnostic runtime rule](../design/framework-agnostic-runtime.md)
makes this a routine operation rather than a migration. If it ever feels like
a migration, the rule has been violated and we need to find where.

## When to run this

- A new base image is released with a security fix (Debian point release,
  OpenSSL CVE, whatever).
- A framework dependency needs a pinned upgrade that can't be hot-patched
  inside running containers.
- After a fresh-install test of a new TinyAgentOS release, to verify the
  upgrade path works end-to-end.
- After any change to `tinyagentos/deployer.py` that alters what gets
  installed into containers.

## Pre-flight

1. Back up `data/` on the host. A single `rsync -a data/ /backups/$(date +%F)/`
   is sufficient because all per-agent state lives under `data/`. If you
   can't do this in one command, state has leaked out of `data/` and that
   leak is a bug worth fixing first.
2. Check `taos agent list` and note the running agents. You'll bring them
   all back up at the end.

## Procedure

```bash
# 1. Stop every agent in parallel.
for name in $(taos agent list --names); do
    taos agent stop "$name" &
done
wait

# 2. Destroy every container. Host-side state is untouched.
for name in $(taos agent list --names); do
    taos agent undeploy "$name"
done

# 3. Pull the new base image.
#    LXC:
incus image refresh images:debian/bookworm
#    Docker/Podman:
docker pull debian:bookworm-slim

# 4. Redeploy every agent. Same name, same framework, same mounts —
#    the deployer recreates the container against the fresh image and
#    bind-mounts the existing host-side state.
taos agent deploy-all --from-config
```

The `deploy-all --from-config` step walks `data/config.yaml`, finds every
agent entry, and runs `deploy_agent(DeployRequest(...))` against each with
the original framework and memory/cpu settings. Because nothing in the
container was stateful, the new containers are bit-identical to fresh
installs except they immediately see the old workspace and memory on mount.

## Expected duration

| Agents | Expected time | Bottleneck |
|---|---|---|
| 1 | ~2 minutes | One apt-get + one pip install |
| 10 | ~5 minutes | pip parallelism is poor; run sequentially |
| 50 | ~20 minutes | Dominated by pip. Worth investigating a wheelhouse cache. |

## Verification

After the upgrade, sanity-check a handful of agents:

```bash
# Workspace files should be present and unchanged.
taos agent exec my-agent -- ls -la /workspace

# Memory files should be present.
taos agent exec my-agent -- ls -la /memory

# Skill / user-memory / LLM proxy URLs should be re-injected.
taos agent exec my-agent -- env | grep -E "TAOS_|OPENAI_"

# The agent should answer a basic prompt.
taos agent chat my-agent "What's in your workspace?"
```

If any of these fail, open an issue with the runbook step that broke.

## Test

`tests/test_container_upgrade.py` runs this flow end-to-end against a
synthetic agent: writes a known file, upgrades, asserts the file is still
there. The test exercises the whole `deploy_agent` path, so it also
implicitly validates that `framework-swap.md` still holds.

## If the rule has been violated

Symptoms that indicate state leaked into a container image somewhere:

- An agent comes back with empty memory after upgrade.
- An agent's workspace has older files than the backup.
- A secret grant is missing.
- A conversation history shows a gap.

None of these should be possible under the rule. If any of them happen,
work backwards from the symptom: the thing that was lost was being stored
somewhere that wasn't mounted. Find the write site, move the storage
to `data/` on the host, and add a test to
`tests/test_framework_agnostic_runtime.py` that would have caught it.
