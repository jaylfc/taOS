# Distributed Compute Cluster — Design Spec

**Date:** 2026-04-05
**Status:** Draft
**Amended:** 2026-04-11 — cluster worker capabilities follow **backend-driven
discovery**: a worker advertises "I can do image-generation, embedding,
llm-chat" by live-probing its own backends on startup and on heartbeat, not
by declaring a static config. The controller's view of "what can the cluster
do right now" is a union of every worker's live backend catalog. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

## The Insight

People can't afford a single high-end AI machine. But they often have:
- A gaming PC (RTX 3060, 16GB RAM)
- A laptop (integrated GPU, 16GB RAM)  
- A Raspberry Pi or two (8GB, CPU only)
- Maybe an Orange Pi (16GB, NPU)

TinyAgentOS should let them combine all of this into one useful AI compute platform.

## How It Differs from Exo

**Exo** splits ONE model across multiple devices (pipeline parallel). This is slow because every token requires network round-trips between devices. Interactive use is impractical.

**TinyAgentOS Cluster** splits DIFFERENT TASKS across devices (task parallel). Each device runs what it's best at independently:
- Pi runs the embedding model (300MB, CPU is fine)
- Laptop runs a small chat model (4B, CPU/iGPU)
- Gaming PC runs the big model (8B+, GPU) and image generation
- Each request goes to ONE device — no network latency in the inference loop

This is **much more practical** for consumer hardware.

## Architecture

```
                    ┌─────────────────────────┐
                    │  TinyAgentOS Controller  │
                    │    (any device)          │
                    │                          │
                    │  ├── Cluster Manager     │
                    │  ├── Task Router         │
                    │  └── Web Dashboard       │
                    └──────────┬──────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼────────┐ ┌────▼────────┐ ┌─────▼───────┐
     │  Gaming PC       │ │  Laptop     │ │  Pi 4       │
     │  RTX 3060 12GB   │ │  16GB RAM   │ │  8GB RAM    │
     │                  │ │             │ │             │
     │  Tasks:          │ │  Tasks:     │ │  Tasks:     │
     │  - Qwen3-8B chat │ │  - Qwen3-4B │ │  - Embedding│
     │  - Image gen     │ │  - Reranking│ │  - qmd serve│
     │  - Reranking     │ │             │ │  - Memory   │
     └──────────────────┘ └─────────────┘ └─────────────┘
```

## Worker Registration

Each device runs a TinyAgentOS worker agent:
```bash
# On the gaming PC
tinyagentos worker --controller http://controller:6969 --name gaming-pc

# On the laptop  
tinyagentos worker --controller http://controller:6969 --name laptop

# On the Pi
tinyagentos worker --controller http://controller:6969 --name pi-4
```

The worker:
1. Connects to the controller
2. Reports its hardware profile (CPU, RAM, GPU, NPU)
3. Reports available inference backends (ollama, rkllama, llama.cpp)
4. Reports loaded models
5. Accepts task assignments from the controller

## Task Router

The controller maintains a capability map:

```python
cluster_state = {
    "gaming-pc": {
        "hardware": {"gpu": "nvidia", "vram_mb": 12288, "ram_mb": 16384},
        "backends": [{"type": "ollama", "url": "http://gaming-pc:11434"}],
        "models": ["qwen3-8b", "sdxl-turbo"],
        "capabilities": ["chat", "image-generation", "reranking"],
        "load": 0.3,  # current utilization
    },
    "laptop": {
        "hardware": {"gpu": "none", "ram_mb": 16384},
        "backends": [{"type": "ollama", "url": "http://laptop:11434"}],
        "models": ["qwen3-4b"],
        "capabilities": ["chat"],
        "load": 0.1,
    },
    "pi-4": {
        "hardware": {"gpu": "none", "ram_mb": 8192},
        "backends": [{"type": "llama-cpp", "url": "http://pi-4:8080"}],
        "models": ["embeddinggemma-300m"],
        "capabilities": ["embedding", "memory"],
        "load": 0.05,
    },
}
```

When a request comes in (embed, chat, rerank, image gen), the router:
1. Checks which workers have the required capability
2. Picks the one with lowest load and highest priority
3. Routes the request directly to that worker's backend
4. Falls back to next worker if the first fails

## Dynamic Migration

When a worker goes offline (user starts gaming, laptop closes):
1. Controller detects via heartbeat timeout (10s)
2. Reassigns workloads:
   - If gaming PC goes down: chat falls back to laptop (smaller model, slower but works)
   - If Pi goes down: embedding moves to laptop
3. Notifies the user via the notification system
4. When worker comes back: redistributes for optimal placement

## Cluster Dashboard

New page in TinyAgentOS: `/cluster`
- Visual map of all workers with hardware specs
- Real-time utilization bars per worker
- Task assignment visualization (which worker handles what)
- Add/remove workers
- Manual task reassignment (drag tasks between workers)

## Worker Communication

Workers communicate with the controller via:
- **HTTP** for registration, heartbeat, task assignment
- **Tailscale** for secure cross-network connectivity (LAN or remote)
- Worker sends heartbeat every 5 seconds
- Controller sends task assignments as HTTP redirects (client → controller → redirect to worker backend)

Or better: controller acts as a **reverse proxy**, accepting all inference requests and routing them to the right worker transparently. Agents don't need to know about the cluster — they just talk to the controller.

## Implementation Phases

### Phase 1: Worker Registration + Heartbeat
- Worker CLI command
- Controller `/api/cluster/workers` endpoint
- Hardware profile exchange
- Heartbeat monitoring

### Phase 2: Task Routing
- Route `/embed` requests to capable workers
- Route `/chat` requests to most powerful available
- Fallback chain on failure

### Phase 3: Dynamic Migration  
- Worker offline detection
- Automatic workload reassignment
- User notification

### Phase 4: Cluster Dashboard
- Visual cluster management page
- Real-time utilization
- Manual task assignment

## Key Small Models for Edge Workers (Researched)

| Model | Params | RAM | Tool Calling | Best For |
|-------|--------|-----|-------------|----------|
| Ministral-3B | 3B | 3GB | Yes (built-in) | Edge tool calling |
| Qwen3-4B | 4B | 3GB | Yes | Reasoning, rivals 72B |
| Qwen3-30B-A3B MoE | 3B active | 4GB | Yes | Best quality/size ratio |
| Llama 3.2-1B | 1B | 2GB | Limited | Lightest option |
| Llama 3.2-3B | 3B | 6GB | Yes | ARM-optimized |
| SmolLM2 | 1.7B | 2GB | Limited | Best quality among SLMs |
| embeddinggemma-300M | 300M | 0.5GB | N/A | Embedding on Pi |
