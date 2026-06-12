# TinyAgentOS App Store & Platform — Design Spec

**Date:** 2026-04-05
**Status:** Draft
**Amended:** 2026-04-11 — the Store's "Available / Installed / Running"
states follow **backend-driven discovery**. "Available" = in the catalog
manifest and compatible with this hardware. "Installed" = the backend
advertising the service is currently reachable. "Running" = a live
capability probe returns OK. The old `installed.json` file is a cache,
not the source of truth. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

## Overview

TinyAgentOS evolves from an agent monitoring dashboard into a full AI-focused home server platform, comparable to Umbrel OS or CasaOS but purpose-built for AI agents. The platform provides an app-store experience for installing agent frameworks, LLM models, and infrastructure services, with pre-built OS images for supported hardware.

## Goals

1. **App Store UX** — browse, install, update, remove apps (agents, models, services) through the web GUI
2. **Model Management** — browse, download, and assign LLM models to agents, with hardware-aware recommendations
3. **Agent Deployment** — create agents through the GUI with automatic LXC provisioning
4. **Hardware Awareness** — auto-detect hardware (NPU, GPU, RAM, CPU), recommend compatible apps/models, adapt to 4GB–128GB+ devices
5. **Pre-built Images** — Armbian-based OS images for supported SBCs with everything pre-installed
6. **Extensible** — community can contribute apps via PR to the catalog repo

## Non-Goals (This Spec)

- Cloud services (tinyagentos.com, email relay) — future spec
- Built-in services (Gitea, mail, web IDE) — these are apps in the catalog, not platform features
- Custom domain management — future spec

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TinyAgentOS Platform                      │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐ │
│  │ Dashboard │  │App Store │  │  Model    │  │  Agent    │ │
│  │ (existing)│  │ Browser  │  │  Manager  │  │ Deployer  │ │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────┘ │
│       │              │              │              │        │
│  ┌────┴──────────────┴──────────────┴──────────────┴─────┐  │
│  │                  Platform Core                         │  │
│  │  • App Registry (manifest parsing, lifecycle)          │  │
│  │  • Hardware Detector (NPU, GPU, RAM, CPU profiling)    │  │
│  │  • Model Manager (download, convert, storage)          │  │
│  │  • Container Manager (LXC create/start/stop/destroy)   │  │
│  │  • Catalog Sync (git pull from app-catalog repo)       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                             │
│  Existing services:                                         │
│  ├── Metrics Store (SQLite)                                 │
│  ├── Backend Adapters (rkllama, ollama, llama.cpp, vllm)    │
│  ├── QMD Client (per-agent memory, HTTP :7832)              │
│  ├── taosmd Client (user memory + librarian, HTTP :7900)    │
│  └── Health Monitor (background poller)                     │
└─────────────────────────────────────────────────────────────┘
```

## App Manifest Format

Every installable unit — agent framework, model, or service — is defined by a YAML manifest.

### Common Fields (All App Types)

```yaml
id: unique-app-id            # lowercase, hyphens, globally unique
name: Human Readable Name
type: agent-framework | model | service | plugin
version: 1.0.0               # semver
description: Short description
icon: icon.png                # relative to manifest dir, or URL
homepage: https://github.com/...
license: MIT

requires:
  ram_mb: 512                 # minimum system RAM needed (not counting model)
  disk_mb: 200                # disk space for installation
  python: ">=3.10"            # optional, if Python is needed
  ports: [3000, 8080]         # optional, ports that will be used

hardware_tiers:               # what this app can do at each hardware level
  arm-npu-16gb: full          # full | degraded | unsupported
  arm-npu-32gb: full
  x86-cuda-12gb: full
  x86-vulkan-8gb: full
  x86-vulkan-4gb: degraded
  cpu-only: degraded
```

### Agent Framework Manifest

```yaml
id: smolagents
name: SmolAgents
type: agent-framework
version: 1.0.0
description: "HuggingFace's code-based agent framework — 30% fewer LLM calls"
homepage: https://github.com/huggingface/smolagents

requires:
  ram_mb: 256
  python: ">=3.10"

install:
  method: pip
  package: smolagents
  extras: []

config_schema:                # fields shown in GUI when creating an agent with this framework
  - name: model
    type: model-select        # special type: shows installed models as dropdown
    label: LLM Model
    required: true
  - name: tools
    type: multiselect
    label: Available Tools
    options: [web_search, file_read, code_exec, memory_search]

hardware_tiers:
  arm-npu-16gb: full
  cpu-only: full
```

### Model Manifest

```yaml
id: qwen3-8b
name: Qwen 3 8B
type: model
version: 3.0.0
description: "General-purpose chat model, strong tool calling"
family: qwen3
homepage: https://huggingface.co/Qwen/Qwen3-8B
capabilities: [chat, tool-calling, code]

variants:
  - id: q4_k_m
    name: Q4_K_M (4-bit, 4.8GB)
    format: gguf
    size_mb: 4800
    min_ram_mb: 6144
    download_url: https://huggingface.co/...
    sha256: abc123...
    backend: [ollama, llama-cpp, vllm]

  - id: q8_0
    name: Q8_0 (8-bit, 8.5GB)
    format: gguf
    size_mb: 8500
    min_ram_mb: 10240
    download_url: https://huggingface.co/...
    sha256: def456...
    backend: [ollama, llama-cpp, vllm]

  - id: rkllm-w8a8
    name: RKLLM W8A8 (NPU, 8.5GB)
    format: rkllm
    size_mb: 8500
    min_ram_mb: 0             # uses NPU memory, not system RAM
    download_url: https://huggingface.co/...
    sha256: ghi789...
    backend: [rkllama]
    requires_npu: [rk3588, rk3576]

hardware_tiers:
  arm-npu-16gb: {recommended: rkllm-w8a8, fallback: q4_k_m}
  arm-npu-32gb: {recommended: rkllm-w8a8, fallback: q8_0}
  x86-cuda-12gb: {recommended: q4_k_m}
  x86-vulkan-8gb: {recommended: q4_k_m}
  x86-vulkan-4gb: unsupported
  cpu-only: {recommended: q4_k_m, notes: "Slow but functional"}
```

### Service Manifest

```yaml
id: gitea
name: Gitea
type: service
version: 1.22.0
description: "Self-hosted Git server with agent access"
homepage: https://gitea.io
icon: gitea.png

requires:
  ram_mb: 256
  disk_mb: 500
  ports: [3000]

install:
  method: container
  image: gitea/gitea:1.22
  volumes:
    - data:/data
    - config:/etc/gitea
  env:
    GITEA__server__ROOT_URL: "http://${HOST_IP}:3000"
    GITEA__server__SSH_PORT: "2222"

lifecycle:
  post_install: scripts/gitea-setup.sh    # auto-create admin, agent accounts
  health_check: "curl -sf http://localhost:3000/api/healthz"
  backup: scripts/gitea-backup.sh

hardware_tiers:
  arm-npu-16gb: full
  cpu-only: full
```

## App Catalog

The catalog is a Git repository (`tinyagentos/app-catalog`) containing manifest files organized by type:

```
app-catalog/
├── catalog.yaml             # index: list of all apps with id, version, type
├── agents/
│   ├── smolagents/
│   │   ├── manifest.yaml
│   │   ├── icon.png
│   │   └── scripts/         # optional install/setup scripts
│   ├── pocketflow/
│   ├── openclaw/
│   ├── tinyagent/
│   └── agent-zero/
├── models/
│   ├── qwen3-0.6b-embedding/
│   ├── qwen3-0.6b-reranker/
│   ├── qwen3-8b/
│   ├── qwen3-4b/
│   ├── qwen3-1.7b/
│   └── llama3-8b/
├── services/
│   ├── gitea/
│   ├── code-server/
│   ├── tailscale/
│   └── mailserver/
└── plugins/
    ├── web-search/
    └── notion-integration/
```

**Catalog sync:** On first boot and periodically (daily), TinyAgentOS runs `git pull` on the local clone of the catalog repo. Users can also add custom catalog directories for private/internal apps.

**catalog.yaml** — lightweight index so the GUI doesn't need to parse every manifest:

```yaml
version: 1
updated: 2026-04-05T12:00:00Z
apps:
  - id: smolagents
    type: agent-framework
    version: 1.0.0
    name: SmolAgents
    description: "HuggingFace's code-based agent framework"
  - id: qwen3-8b
    type: model
    version: 3.0.0
    name: Qwen 3 8B
    description: "General-purpose chat, strong tool calling"
  # ...
```

## Hardware Detection

On first boot (and on-demand from settings), TinyAgentOS profiles the hardware:

```python
{
    "profile": "arm-npu-32gb",       # auto-generated profile ID
    "cpu": {
        "arch": "aarch64",
        "model": "Cortex-A76/A55",
        "cores": 8,
        "soc": "rk3588"
    },
    "ram_mb": 32768,
    "npu": {
        "type": "rknpu",              # rknpu | hailo | coral | qualcomm | none
        "device": "/dev/rknpu",
        "tops": 6,
        "cores": 3
    },
    "gpu": {
        "type": "mali",               # mali | nvidia | amd | intel | none
        "model": "Mali-G610",
        "vram_mb": 0,                 # shared memory, no dedicated VRAM
        "vulkan": false,              # needs kernel 6.13+ for PanVK
        "cuda": false,
        "rocm": false
    },
    "disk": {
        "total_gb": 256,
        "free_gb": 180,
        "type": "emmc"               # emmc | sd | nvme | ssd | hdd
    },
    "os": {
        "distro": "armbian",
        "version": "25.8",
        "kernel": "6.1.115-vendor-rk35xx"
    }
}
```

**Detection methods:**
- CPU: `/proc/cpuinfo`, `lscpu`
- RAM: `/proc/meminfo`
- NPU: check for `/dev/rknpu` (Rockchip), `/dev/hailo*` (Hailo), `/dev/apex_*` (Coral)
- GPU: `lspci` for discrete, `/sys/class/drm` for integrated, check Vulkan via `vulkaninfo`
- Disk: `df`, `lsblk`
- OS: `/etc/os-release`, `uname -r`

**Hardware profile mapping:**
The hardware profile determines which model variants are recommended and which apps are compatible. Profile IDs follow the pattern: `{arch}-{accelerator}-{ram}gb`.

| Profile | Example Hardware | Model Strategy |
|---------|-----------------|----------------|
| arm-npu-16gb | Orange Pi 5 Plus (RK3588) | Embed/rerank/expand on NPU, chat on CPU or offload |
| arm-npu-32gb | Orange Pi 5 Plus 32GB | More models on NPU simultaneously |
| arm-npu-64gb+ | RK3588 boards with 64GB+ | Full model suite on NPU + large context |
| x86-cuda-12gb | Budget PC + RTX 3060 | GGUF on GPU, fast inference |
| x86-cuda-24gb | PC + RTX 3090/4090 | Large models, multiple concurrent |
| x86-vulkan-4gb | Older NVIDIA (Pascal/Maxwell) | Small models only |
| x86-vulkan-8gb | GTX 1070/1080 | Medium models |
| x86-rocm-12gb | AMD RX 6700 XT | Same as cuda-12gb |
| cpu-only | Any low-end device | Smallest quantized models, slow |

Users with more RAM or NPU cores automatically get access to larger models and more concurrent agents. The platform doesn't artificially limit — it recommends based on what will actually work.

## App Lifecycle

### States

```
available → downloading → installing → installed → running → stopped → uninstalled
                                                      ↑         │
                                                      └─────────┘
```

### Operations

| Operation | What happens |
|-----------|-------------|
| **Install** | Download artifacts, run install method (pip/apt/container/script), register in local DB |
| **Start** | Start the service/agent (systemd unit, container start) |
| **Stop** | Stop gracefully (systemd stop, container stop) |
| **Update** | Pull new version from catalog, re-install, restart |
| **Uninstall** | Stop, remove artifacts, deregister. Optionally keep data volumes. |
| **Configure** | Edit app-specific settings via GUI (rendered from config_schema) |

### Install Methods

| Method | How | Used for |
|--------|-----|----------|
| `pip` | `pip install {package}` in a venv | Agent frameworks, Python tools |
| `apt` | `apt install {packages}` | System packages |
| `container` | Pull and run OCI container (via incus/LXC) | Services (Gitea, mail, etc.) |
| `script` | Run a custom install script from the manifest dir | Complex installs |
| `download` | Download file to models directory | Models (GGUF, RKLLM files) |

### Model-Specific Operations

| Operation | What happens |
|-----------|-------------|
| **Download** | Fetch model file from URL, verify SHA256, store in `~/.cache/tinyagentos/models/` |
| **Convert** | For RKLLM: run conversion toolkit (requires x86 host or pre-converted) |
| **Assign** | Link a downloaded model to a backend (ollama pull, copy to rkllama models dir) |
| **Unload** | Remove from active backend, keep file on disk |
| **Delete** | Remove file from disk |

## Agent Deployment

Creating an agent through the GUI:

1. **Choose framework** — pick from installed agent frameworks (or install one)
2. **Choose model** — pick from downloaded models compatible with hardware
3. **Configure** — fill in framework-specific settings (rendered from `config_schema`)
4. **Name and customize** — agent name, color, description
5. **Deploy** — TinyAgentOS:
   - Creates an LXC container for the agent
   - Installs the agent framework inside
   - Installs QMD and starts `qmd serve` (pointing at host's inference backend)
   - Configures the agent with chosen model and settings
   - Starts the agent
   - Registers in TinyAgentOS config

### LXC Container Template

Each deployed agent gets a minimal LXC container with:
- Base OS (Debian bookworm minimal)
- Node.js (for QMD)
- Python (for agent frameworks)
- QMD installed with `qmd serve` running on port 7832
- Agent framework installed
- Systemd services for both QMD and the agent

The container template is pre-built as part of the TinyAgentOS image build process, so agent creation is fast (seconds, not minutes of package installation).

## Platform Storage Layout

```
/opt/tinyagentos/                    # platform installation
├── app-catalog/                     # git clone of catalog repo
├── apps/                            # installed app data
│   ├── smolagents/                  # per-app directory
│   │   ├── venv/                    # Python virtual environment
│   │   └── config.yaml             # app-specific config
│   └── gitea/
│       └── config.yaml
├── models/                          # downloaded model files
│   ├── qwen3-8b-q4_k_m.gguf
│   ├── qwen3-embedding-0.6b.rkllm
│   └── checksums.json
├── templates/                       # LXC container templates
│   └── agent-base.tar.gz
└── data/
    ├── config.yaml                  # main platform config
    ├── hardware.json                # hardware detection results
    ├── installed.json               # installed apps registry
    └── metrics.db                   # metrics SQLite
```

## Pre-Built OS Images

### Build System

Fork [armbian/build](https://github.com/armbian/build) as `tinyagentos/os-build`. Customizations live in `userpatches/`:

```
userpatches/
├── customize-image.sh              # main customization script
├── overlay/                        # files copied into image
│   ├── opt/tinyagentos/            # pre-installed platform
│   ├── etc/systemd/system/
│   │   ├── tinyagentos.service
│   │   └── rkllama.service         # for NPU boards
│   └── etc/skel/.bashrc            # user shell config
└── extensions/
    └── tinyagentos.sh              # build extension hooks
```

**customize-image.sh** handles:
- Install Python 3.12, Node.js 22, pip, git
- Install TinyAgentOS Python package
- Install QMD globally
- Install rkllama (on NPU-capable boards)
- Pre-download base models (embedding, reranker) for the target hardware
- Configure systemd services
- Set up first-boot wizard trigger
- Clone app-catalog repo

### Supported Boards (Initial)

| Board | SoC | NPU | Priority |
|-------|-----|-----|----------|
| Orange Pi 5 Plus | RK3588 | RKNPU 6 TOPS | Primary — development board |
| Orange Pi 5 | RK3588S | RKNPU 6 TOPS | High — popular, cheaper |
| Rock 5B | RK3588 | RKNPU 6 TOPS | High — well-supported |
| Raspberry Pi 5 | BCM2712 | None (Hailo via M.2) | Medium — huge community |
| x86_64 generic | Any | Varies | Medium — Debian image |

Additional boards added as community contributes and tests. The Armbian build framework makes adding a new board straightforward — it's mostly config, not code.

### Kernel Requirements

| Feature | Minimum Kernel | Notes |
|---------|---------------|-------|
| RKNPU | 5.10+ (vendor) | Rockchip vendor kernel required, not mainline |
| Mali GPU (Vulkan) | 6.13+ | PanVK driver for Mali-G610 |
| Hailo-8 | 5.15+ | Hailo kernel module |
| Incus/LXC | 5.15+ | For container management |

Pre-built images pin to the correct kernel for each board. The vendor kernel is used for NPU boards (even though it's older) because NPU support requires it. Users don't need to think about this.

### Image Variants

Per supported board, two image variants:

| Variant | Contents | Size | For |
|---------|----------|------|-----|
| **tinyagentos-full** | OS + platform + base models pre-downloaded | ~4-6GB | Flash and go, everything works immediately |
| **tinyagentos-lite** | OS + platform, models downloaded on first boot | ~1-2GB | Smaller download, requires internet on first boot |

### Update Strategy

- **Platform updates:** `pip install --upgrade tinyagentos` or via the GUI's settings page
- **OS updates:** `apt upgrade` — Armbian's standard update path, kernel pinned to prevent breaking NPU
- **Catalog updates:** `git pull` on the app-catalog repo (automatic, daily)
- **Kernel updates:** Only via TinyAgentOS-specific apt repo to ensure NPU compatibility. Users should NOT run `armbian-config` kernel upgrades blindly.

## First Boot Experience

When the user boots a pre-built image for the first time:

1. **Hardware detection** runs automatically, stores results in `hardware.json`
2. **Welcome screen** in the browser (TinyAgentOS starts automatically on port 6969)
3. **Network setup** — optionally configure Tailscale for remote access
4. **Create first agent** — guided wizard:
   - Shows recommended models for detected hardware
   - Downloads selected model
   - Picks agent framework
   - Names the agent
   - Deploys into LXC container
5. **Dashboard** — agent is running, memory is empty, ready for first conversation

The Setup Agent (Phase 2 local LLM) could eventually replace steps 3-4 with a natural language chat experience.

## Web GUI Changes

### New Pages

**App Store** (`/store`)
- Grid of available apps with icons, descriptions, install buttons
- Filter by type (agents, models, services, plugins)
- Filter by compatibility (auto-filtered based on hardware profile)
- Installed apps shown with update/uninstall options
- Search bar

**Model Manager** (`/models`)
- List of downloaded models with size, format, assigned backends
- Download new models (from catalog, with progress bar)
- Assign/unassign models to backends
- Hardware-aware recommendations (green = good fit, yellow = will work, red = too large)

**Agent Deployer** (part of `/agents`, expanded)
- Current agent table (existing)
- "Create Agent" wizard:
  - Step 1: Choose framework (from installed, or install new)
  - Step 2: Choose model (from downloaded, or download new)
  - Step 3: Configure (rendered from framework's config_schema)
  - Step 4: Name, color, deploy
- Per-agent: start/stop/restart/logs/shell access

**System Settings** (`/settings`, replaces `/config`)
- Hardware profile display
- Storage usage (models, agents, services)
- Catalog sync settings (repo URL, auto-update frequency)
- Network settings (Tailscale, hostname)
- Platform updates
- The existing YAML config editor moves here as an "Advanced" section

### Navigation Update

```
Dashboard | Store | Models | Agents | Settings
```

## API Endpoints (New)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/store/catalog` | List all available apps (from catalog) |
| GET | `/api/store/installed` | List installed apps |
| POST | `/api/store/install` | Install an app by ID |
| POST | `/api/store/uninstall` | Uninstall an app by ID |
| POST | `/api/store/update` | Update an app to latest version |
| GET | `/api/models` | List downloaded models |
| POST | `/api/models/download` | Download a model variant |
| DELETE | `/api/models/{id}` | Delete a downloaded model |
| GET | `/api/hardware` | Hardware detection results |
| POST | `/api/hardware/detect` | Re-run hardware detection |
| POST | `/api/agents/deploy` | Create and deploy a new agent (LXC + framework + qmd) |
| POST | `/api/agents/{name}/start` | Start agent container |
| POST | `/api/agents/{name}/stop` | Stop agent container |
| GET | `/api/agents/{name}/logs` | Stream agent container logs |

## NPU Support Matrix

The platform abstracts NPU differences behind the backend adapter layer. Current and planned:

| NPU Type | Detection | LLM Backend | Status |
|----------|-----------|-------------|--------|
| Rockchip RKNPU (RK3588/3576) | `/dev/rknpu` | rkllama, rk-llama.cpp | Working |
| Hailo-8/8L | `/dev/hailo*` | None for LLM yet | Vision only, future |
| Google Coral | `/dev/apex_*` | None for LLM | Vision only, future |
| Qualcomm Hexagon | TBD | llama.cpp QNN backend | Emerging |
| AMD XDNA (Ryzen AI) | TBD | llama.cpp XDNA backend | Emerging |

The manifest `requires_npu` field is a list of supported NPU types, making it forward-compatible. As new NPU backends get LLM support, we add them to the adapter layer and update model manifests with new variants.

## Resource Budget

### Real Measurements (Orange Pi 5 Plus, 16GB, April 2026)

| Component | RSS (Measured) | Notes |
|-----------|---------------|-------|
| Base Armbian (kernel + systemd + essential) | ~200 MB | Headless, no desktop |
| TinyAgentOS (FastAPI + uvicorn) | 67 MB | Very lean |
| incusd (container daemon) | 67 MB | Lighter than expected |
| qmd serve (Node.js) | 85-90 MB | Per instance |
| rkllama (Python + model weights) | ~5.8 GB (4 processes) | Model weights loaded into system RAM for NPU transfer — dominates everything |

### Platform Overhead Target: < 500MB

"Platform overhead" = base OS + TinyAgentOS + container management. Does NOT include inference backends (rkllama/ollama) or agent containers.

| Component | Budget |
|-----------|--------|
| Armbian base | 200 MB |
| TinyAgentOS | 70 MB |
| Container management (raw lxc tools, no incusd) | 0 MB |
| Metrics SQLite + health monitor | ~5 MB |
| **Platform total** | **~275 MB** |

With incusd (if needed for richer container management): add 70MB → **~345 MB**.

### Per-Agent Overhead

| Deployment Mode | RAM per Agent | When to Use |
|-----------------|--------------|-------------|
| Process mode (venv + systemd) | 40-100 MB | Lightweight frameworks (PocketFlow, picoclaw, nanoclaw) |
| LXC container (Alpine + qmd serve + framework) | 100-180 MB | Full frameworks needing isolation (OpenClaw, SmolAgents) |
| LXC container (Debian + qmd serve + framework) | 150-260 MB | Frameworks needing glibc (agent-zero, heavy Python deps) |

### Shared vs Per-Agent QMD Serve

**Default: shared qmd serve on host.** One Node.js process (~90MB) serves all agents, with per-agent data isolated by database path. This saves 90MB per additional agent.

**Per-agent qmd serve** available as an option for multi-host deployments where agent data must travel with the container. Adds ~90MB per agent.

### Recommended Limits by Hardware Tier

| RAM | Platform | Inference Backend | Agents (process) | Agents (container) | Available for Models |
|-----|----------|-------------------|-------------------|--------------------|---------------------|
| 4 GB | 275 MB | ~500 MB (rkllama) | 1-2 | 1 | ~2.5 GB |
| 8 GB | 275 MB | ~500 MB | 3-5 | 2-3 | ~6 GB |
| 16 GB | 275 MB | ~500 MB | 5-10 | 4-6 | ~14 GB |
| 32 GB | 275 MB | ~500 MB | 10-20 | 8-12 | ~30 GB |
| 64 GB+ | 275 MB | ~500 MB | 20+ | 16+ | ~62 GB |

### Pre-Install Resource Check

Before any install, deploy, or start operation, the platform checks:
1. Current available RAM vs `requires.ram_mb` (the full deployment cost, including container overhead)
2. Available disk vs `requires.disk_mb`
3. If insufficient: warn the user with specific numbers, allow override

### Container Memory Limits

Every agent container is created with `memory.limit` set based on hardware tier:
- 4 GB board: 512 MB per container
- 8 GB: 1 GB per container
- 16 GB: 2 GB per container
- 32 GB+: 4 GB per container

Prevents a single misbehaving agent from OOMing the host.

### Container Base Images

Two container templates, chosen per-framework:

| Template | Base | Size | For |
|----------|------|------|-----|
| **agent-alpine** | Alpine 3.x + Node.js + Python | ~15 MB base | PocketFlow, picoclaw, nanoclaw, TinyAgent |
| **agent-debian** | Debian bookworm-slim + Node.js + Python | ~60 MB base | OpenClaw, SmolAgents, agent-zero (need glibc) |

### rkllama Memory Investigation

rkllama currently loads model weights into system RAM (5.8GB RSS for 3 preloaded models) before transferring to NPU. This is the single largest consumer on the system. Investigate:
- Whether RKLLM API supports direct-to-NPU loading without staging in system RAM
- Whether model weights can be memory-mapped (mmap) to reduce RSS
- rk-llama.cpp as alternative backend (may have different memory behavior)

## Security Considerations

- **App manifests are reviewed** — the official catalog is curated, not an open free-for-all
- **Container isolation** — full agent frameworks run in LXC containers; lightweight frameworks can run as sandboxed processes
- **Container management** — raw `lxc-*` tools by default (zero daemon overhead); incusd available as optional app for richer management
- **Model checksums** — SHA256 verified on download
- **No auth for MVP** — trusted LAN/Tailscale network (auth is a future spec)
- **Install scripts run as platform user** — not root, unless container method

## Future Work (Separate Specs)

1. **Cloud services** — tinyagentos.com, email relay, hosted backup
2. **Setup Agent** — chat-based configuration via local LLM
3. **Authentication** — user accounts, API keys
4. **Remote management** — manage multiple TinyAgentOS instances
5. **LoRA pipeline** — fine-tune → convert → deploy workflow in the GUI
6. **Dynamic NPU allocation** — smart model loading/unloading based on demand
