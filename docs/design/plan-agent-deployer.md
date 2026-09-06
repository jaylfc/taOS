# Platform Plan 4: Agent Deployer — Design Notes

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

## Overview

The Agent Deployer creates agents through the GUI: pick framework → pick model → configure → deploy into LXC container.

## LXC Container Management

Uses `incus` CLI (already installed on the Orange Pi). Key commands:

```bash
# Create container from image
incus launch images:debian/bookworm agent-naira

# Or from Alpine for lightweight agents
incus launch images:alpine/3.20 agent-naira

# Set memory limit
incus config set agent-naira limits.memory 2GB

# Set CPU limit
incus config set agent-naira limits.cpu 2

# Get container IP
incus list agent-naira -f json | jq '.[0].state.network.eth0.addresses[0].address'

# Execute command inside container
incus exec agent-naira -- apt update
incus exec agent-naira -- apt install -y python3 python3-pip nodejs npm

# Push file into container
incus file push local-file agent-naira/path/in/container

# Start/stop
incus start agent-naira
incus stop agent-naira

# Delete
incus delete agent-naira --force
```

## Agent Deployment Flow

1. **User fills wizard form** (framework, model, name, config)
2. **Platform checks resources** (RAM available vs framework + model needs)
3. **Create LXC container** via `incus launch`
4. **Install dependencies** inside container (Node.js, Python, QMD, framework)
5. **Configure QMD serve** pointing at host's inference backend
6. **Configure agent** with chosen model and settings
7. **Create systemd services** inside container (qmd-serve, agent)
8. **Start services**
9. **Register agent** in TinyAgentOS config
10. **Update dashboard**

## Container Templates

Pre-build container images with common deps to speed up deployment:

```bash
# Create template once
incus launch images:debian/bookworm agent-template
incus exec agent-template -- apt update
incus exec agent-template -- apt install -y python3 python3-pip python3-venv nodejs npm git curl
incus exec agent-template -- npm install -g @tobilu/qmd
incus stop agent-template
incus publish agent-template --alias tinyagentos/agent-debian
incus delete agent-template

# Deploy from template (fast — seconds not minutes)
incus launch tinyagentos/agent-debian agent-naira
```

## API Endpoints

```
POST /api/agents/deploy    — create and deploy new agent
POST /api/agents/{name}/start
POST /api/agents/{name}/stop
POST /api/agents/{name}/restart
GET  /api/agents/{name}/logs
DELETE /api/agents/{name}          — archive agent (stop + rename container + move dirs)
DELETE /api/agents/archived/{id}   — purge archived agent (irreversible)
```

## Implementation Priority

This depends on:
1. incus being available (it is — already running on the Orange Pi)
2. The container template being built
3. The wizard UI (multi-step form in the agents page)

Can be built and tested without Jay's agent migration — just deploy a test agent.
