# Beads Protocol + Exo Integration Research

**Date:** 2026-04-06

## Beads — Agent Collaboration Protocol

**What it is:** A persistent, structured memory system for coding agents. Replaces loose markdown with a dependency-aware graph. Part of the Gas Town ecosystem for multi-agent orchestration.

**Key features relevant to TinyAgentOS:**
- Hash-based IDs (bd-a1b2) prevent merge collisions in multi-agent workflows
- Semantic "memory decay" — summarises old closed tasks to save context window
- Message issue type with threading, ephemeral lifecycle, mail delegation
- Graph links: relates_to, duplicates, supersedes, replies_to
- Escalation system: agents hit blockers → escalate via tracked beads
- MIT license, active development (20k+ commits)

**Integration approach:**
- Don't replace our inter-agent messaging — augment it
- Beads could be the structured task delegation protocol between agents
- Our AgentMessageStore handles chat, Beads handles task handoffs
- Import beads as a Python package, expose via the workspace API
- Each agent's workspace could show their Beads graph alongside messages

**Assessment:** Worth integrating as an optional collaboration layer. Not critical for MVP but adds structured multi-agent coordination that our simple message store doesn't provide.

## Exo — Distributed Model Inference

**What it is:** Peer-to-peer distributed inference that splits a single model across multiple devices using pipeline/tensor parallelism.

**Key features:**
- Peer-to-peer (no master-worker) — devices auto-discover each other
- OpenAI, Claude, Ollama API compatible
- Supports pipeline AND tensor parallelism
- Dynamic model partitioning based on device resources
- Topology-aware with RDMA over Thunderbolt

**How it differs from our cluster:**
- **Exo:** splits ONE model across devices (pipeline parallel) — for running models too large for any single device
- **TinyAgentOS cluster:** routes DIFFERENT TASKS to different devices (task parallel) — each device runs what it's best at

**These are complementary, not competing:**
- User has a 70B model that doesn't fit any single device → exo splits it across 2-3 machines
- User has embed/rerank/chat as separate tasks → our router sends each to the best device
- Both can coexist in the same cluster

**Integration approach:**
- Add exo as an optional backend type alongside ollama/rkllama/llama-cpp
- When a model is too large for any single worker, offer "Distributed (exo)" as a deployment option
- The cluster dashboard shows: "This model requires 48GB — split across gaming-pc (12GB) + laptop (16GB) + mac (24GB) using exo"
- Requires exo installed on participating workers

**API integration is straightforward:**
- exo exposes OpenAI-compatible API at a single endpoint
- Our backend adapter system already supports OpenAI-compatible endpoints
- Just add an "exo" backend type to the adapter layer

**Assessment:** Integration is low-effort, high-value. Makes the cluster useful for models that exceed any single device's capacity. Experimental today but worth supporting for enthusiasts.

**Updated findings (2026-04-12):**
- License is **Apache-2.0** (not GPL-3.0 -- the old ex-exo repo was GPL)
- No pip package -- requires source build with `uv` + Rust + Node.js
- Default port is **52415** (OpenAI-compat API)
- Python **3.13+** required
- **No ARM64/Rockchip support** -- Apple Silicon and x86 only today
- Upstream issue filed: exo-explore/exo#1878 (ARM64/RK3588 NPU backend)

## Current Integration Status (2026-04-12)

### Done:
- exo registered as backend type in `backend_adapters.py` (OpenAI compat adapter)
- exo in `BACKEND_CAPABILITIES` with `llm-chat` capability
- Worker probe detects exo on port 52415 automatically
- Catalog manifest at `app-catalog/services/exo/manifest.yaml`
- `taos-deploy-helper.sh install-exo` deploys exo on a worker from the controller UI
- README section documenting exo with hardware support matrix

### Remaining:
- Deploy wizard "Distributed (exo)" option when model exceeds single-worker VRAM
- Cluster optimiser suggesting exo for oversized models
- Beads integration for structured task delegation
- Worker-side HTTP server to receive deploy/remote commands from controller
