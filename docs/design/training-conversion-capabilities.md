# Training, Conversion & Dynamic Capabilities — Design Spec

**Date:** 2026-04-06
**Status:** Approved

## Overview

Three interconnected systems:
1. **Dynamic Capability System** — features unlock/lock based on available hardware across the cluster
2. **Training & Fine-Tuning** — LoRA training via web UI, agent self-improvement, per-agent LoRA adapters
3. **Model Conversion Pipeline** — GGUF→RKLLM conversion, pre-converted model hosting, model browser integration
4. **Video Generation Page** — unlocks when GPU worker available

## 1. Dynamic Capability System

Every feature declares hardware requirements. The system checks local hardware + all cluster workers.

### Capability Registry

```python
CAPABILITIES = {
    # Core (always available)
    "keyword-search": {},
    "agent-deploy-process": {"min_ram_mb": 2048},
    
    # Inference
    "chat-small": {"min_ram_mb": 4096},
    "chat-large": {"any_worker": {"min_vram_mb": 8192}},
    "embedding": {"min_ram_mb": 2048},
    "reranking": {"any_worker": {"min_vram_mb": 4096, "or": "rknpu"}},
    "semantic-search": {"requires": "embedding"},
    
    # Generation
    "image-generation-cpu": {"min_ram_mb": 4096},
    "image-generation-gpu": {"any_worker": {"min_vram_mb": 6144}},
    "image-generation-npu": {"requires": "rknpu"},
    "video-generation": {"any_worker": {"min_vram_mb": 6144}},
    "tts": {"min_ram_mb": 2048},
    "stt": {"min_ram_mb": 2048},
    "music-generation": {"min_ram_mb": 4096},
    
    # Training
    "lora-training": {"any_worker": {"min_vram_mb": 8192}},
    "full-training": {"any_worker": {"min_vram_mb": 24576}},
    "agent-retrain": {"requires": "lora-training"},
    
    # Conversion
    "rknn-conversion": {"any_worker": {"arch": "x86_64", "has": "rknn_toolkit"}},
    
    # Cluster
    "multi-worker": {"min_workers": 2},
}
```

### UI Behaviour

Three states per feature:
- **Available** — full colour, functional
- **Locked with hint** — greyed out, shows what's needed to unlock: "🔒 Add a GPU worker with 8GB+ VRAM to enable"
- **Hidden** — features that don't make sense at all (e.g. RKNN conversion hint on a non-ARM setup)

### Dynamic Navigation

Nav items appear/disappear based on capabilities:
- Solo Pi 4: Dashboard, Store, Models, Memory, Agents, Settings (6 items)
- Pi + GPU worker: adds Images, Video, Training (9 items)
- Full cluster: all pages visible (14+ items)

Recalculates on worker join/leave — no restart needed.

### Implementation

`tinyagentos/capabilities.py`:
- `CapabilityChecker` class takes hardware profile + cluster manager
- `is_available(capability)` → bool
- `get_unlock_hint(capability)` → str or None
- `get_available_capabilities()` → list[str]
- Template helper: `{% if cap.is_available("video-generation") %}` or locked state

## 2. Training & Fine-Tuning

### Training Page (`/training`)

Shows when `lora-training` or `full-training` capability is available. When locked, shows the unlock hint.

Two modes:

#### Mode 1: Train New LoRA (Advanced)

1. **Select base model** — from downloaded models, filtered by what fits in available VRAM
2. **Upload dataset** — JSONL, CSV, TXT, or select from agent memory
3. **Configure:**
   - Presets: Quick (1 epoch, small rank), Balanced (3 epochs), Thorough (5+ epochs)
   - Advanced: learning rate, LoRA rank, alpha, target modules
4. **Train** — routed to GPU worker, live progress (loss curve chart, ETA, current epoch)
5. **Review** — show training metrics, sample outputs
6. **Deploy** — select target devices, auto-convert for each backend

#### Mode 2: Retrain Agent (One-Click)

1. **Select agent** from dropdown
2. **Agent self-audit runs automatically:**
   - Reviews conversation history from memory
   - Identifies weak areas (low-confidence responses, repeated questions, topic gaps)
   - Searches for latest reference material (web search, configured documentation URLs)
   - Generates a training plan: recommended sources, estimated improvement areas
3. **User reviews plan** — approve, edit, or add sources
4. **Auto-fetch** — pulls recommended documents, converts to training format
5. **Train LoRA** — runs on GPU worker with progress
6. **Deploy to agent** — the LoRA is assigned specifically to this agent

#### Per-Agent LoRA Adapters

Each agent can have its own LoRA adapter:

```yaml
agents:
  - name: naira
    model: qwen3-8b
    lora: naira-web-design-v3.gguf   # agent-specific adapter
  - name: stanley
    model: qwen3-8b
    lora: stanley-3dprint-v2.gguf    # different specialisation
  - name: mary
    model: qwen3-8b
    lora: null                        # uses base model only
```

At inference time:
- **ollama:** `--lora /path/to/adapter.gguf` — hot-swap per request, instant
- **llama.cpp:** `--lora /path/to/adapter.gguf` — hot-swap per request, instant
- **rkllama (NPU):** LoRA must be merged into base before RKLLM conversion (baked in)

### LoRA Strategy for NPU (rkllama)

The NPU can't hot-swap LoRA adapters — each RKLLM model has weights baked in. Strategy:

1. **Default: LoRAs route to GPU workers** — NPU handles shared models (embed, rerank, expand) for all agents. Agent-specific LoRA chat routes to GPU workers where swapping is free. Solo NPU users get the base model — still good, not specialised.

2. **Advanced: merged RKLLM per agent** — for stable LoRAs that don't change often, merge LoRA into base and convert to RKLLM. Creates a separate model file per agent (e.g. `naira-web-v3.rkllm`). Only one loaded at a time — pool manager time-shares by batching per-agent requests to minimise swaps (5-10s per swap).

3. **Future: dynamic NPU core allocation** (issue #13) — smart scheduling that assigns NPU cores per-agent and manages model loading/unloading based on demand patterns.

### Automatic Method Selection

The system picks the best method transparently based on what's available:

1. **GPU worker available?** → route agent's LoRA chat to GPU (instant hot-swap, preferred)
2. **NPU only, merged RKLLM for this agent exists?** → load merged model on NPU (5-10s swap when switching agents, fine for 2-3 agents)
3. **NPU only, no merged model?** → use shared base model on NPU (no specialisation, no swap delay)

The capability system and task router handle this automatically. Users just see "naira has web design training" — the infrastructure picks the fastest available path.

For time-sharing on NPU, the pool manager batches requests per-agent to minimise swaps. If naira and stanley are both active, it serves all naira's queued requests, swaps to stanley's model, serves his requests, etc. Natural conversation patterns (one agent active at a time) mean swaps are rare in practice.

### Training Data Sources

- Agent conversation memory (via qmd serve)
- Uploaded files (drag-and-drop, same as Import page)
- Web URLs (auto-scraped)
- Documentation repos (git clone + embed)
- Agent's own recommendations (self-audit)

### Training Job Management

```python
@dataclass
class TrainingJob:
    id: str
    agent_name: str | None        # None for general LoRA
    base_model: str
    dataset_path: str
    config: dict                  # lr, epochs, rank, etc
    status: str                   # queued | preparing | training | converting | deploying | complete | failed
    worker_name: str              # which cluster worker runs it
    progress: float               # 0-100
    metrics: dict                 # loss, eval_loss, etc
    output_path: str | None       # resulting LoRA file
    created_at: float
    completed_at: float | None
```

## 3. Model Conversion Pipeline

### Entry Points

**From Model Browser:**
- Pre-converted RKLLM available on HuggingFace → "Download for NPU" (instant)
- x86 worker with RKNN-Toolkit in cluster → "Convert for NPU" (queues job)
- Neither → "🔒 Add an x86 worker to unlock conversion, or check for pre-converted versions"

**From Training:**
- LoRA complete → auto-detect targets → convert for each
- For RKNN: merge LoRA into base → convert merged model → deploy RKLLM

**Standalone Conversion Page** (in Settings or Models):
- Upload any model file → select target format → queue conversion

### Conversion Worker

Runs on x86 machine with RKNN-Toolkit2:

```python
class ConversionJob:
    id: str
    source_model: str             # path or HF model ID
    source_format: str            # gguf | hf | safetensors
    target_format: str            # rkllm | gguf
    target_quantization: str      # w8a8 | w4a16 | q4_k_m
    status: str
    worker_name: str
    progress: float
    output_path: str | None
```

### Pre-Converted Model Hosting

- Jay converts popular models on Fedora box (RKNN-Toolkit v1.2.3)
- Uploads to HuggingFace `jaylfc/` org
- Model browser auto-checks for pre-converted variants
- Manifest in catalog links to pre-converted URLs

## 4. Video Generation Page

### `/video` page — unlocks when `video-generation` capability available

Layout:
- Prompt input (text description of video)
- Model selector (WanGP, LTX Video — from installed video gen apps)
- Duration slider (2-10 seconds)
- Resolution selector
- Generate button → routes to GPU worker
- Gallery of generated videos (like image gallery but with video player)
- Download button

When locked: "🔒 Connect a machine with 6GB+ GPU to enable video generation"

### Backend

Similar to image generation — calls the video gen service running on the GPU worker. WanGP exposes a Gradio API, LTX Video has a Python API. Route through the cluster task router.

## Implementation Priority

1. **Capability system** — foundation for everything else
2. **Dynamic nav + locked feature UI** — immediate UX improvement
3. **Video generation page** — quick win, similar to images page
4. **Conversion pipeline** — model browser integration
5. **LoRA training page** — most complex
6. **Agent retrain** — builds on LoRA training + agent memory
