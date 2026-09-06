# RKNN Image Generation on RK3588 — Deep Dive & Enhancement Proposals

**Date:** 2026-04-05

## Current Landscape

### What Exists

There are four separate projects for image generation on RK3588, each taking a different approach:

| Project | Approach | Speed (512x512) | Status |
|---------|----------|-----------------|--------|
| **[rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo)** | Official Rockchip examples, Python RKNN API | ~8s (256x256) | Active, maintained by Rockchip |
| **[LCM-Dreamshaper-V7-rs](https://github.com/darkautism/LCM-Dreamshaper-V7-rs)** | Rust, all 3 NPU cores, LCM 4-step | ~few seconds (512x512) | Active, fastest implementation |
| **[RK3588-stable-diffusion-GPU](https://github.com/happyme531/RK3588-stable-diffusion-GPU)** | Mali-G610 GPU via MLC/TVM | Unknown | Less active, requires PanVK |
| **rkllama** (`/v1/images/generations`) | OpenAI-compatible API, RKNN runtime | Depends on model | Built-in to rkllama, already running |

### What's Missing (The Gap)

1. **No web UI** — all four projects are CLI/API only. No browser-based interface for prompting, viewing results, managing generation history.
2. **No unified model management** — each project has its own model download/conversion process. No central way to browse available SD models, download them, convert to RKNN format, and deploy.
3. **No integration between them** — rkllama has the API, the Rust project has the speed, the model zoo has the breadth. They don't talk to each other.
4. **No prompt queue/history** — generate one image at a time, no batch queue, no history of past generations.
5. **The conversion pipeline is painful** — converting PyTorch/ONNX → RKNN requires the x86-only RKNN-Toolkit2. Can't convert on the SBC itself. No web-based conversion workflow.

### rkllama Already Has Image Generation

rkllama (our fork at `jaylfc/rkllama`) already has a `/v1/images/generations` endpoint (OpenAI-compatible). It:
- Accepts prompt, model, size, seed, num_inference_steps, guidance_scale
- Uses per-model locking (our contribution)
- Returns base64 or URL responses
- Delegates to `GenerateImageEndpointHandler`

This means **TinyAgentOS can already generate images via rkllama** without any new backend work. The gap is the web UI and model management.

### Performance Benchmarks (Measured)

From the LCM-Dreamshaper-V7-rs project and Radxa docs:
- **Text encoding:** 0.05-0.08s
- **UNet inference (per step):** 2.36s at 384x384
- **VAE decode:** 3.15-5.48s
- **Total (4 LCM steps, 256x256):** ~8s
- **Total (4 LCM steps, 512x512):** ~15-20s estimated

For comparison, an RTX 3060 does 512x512 in ~2-3s. The RK3588 NPU is 5-10x slower but **it works** — and for a $150 SBC that's running your AI agents, memory search, AND generating images, that's remarkable.

### The Attention Problem

The main technical limitation: Stable Diffusion's text encoder (CLIP) uses multi-headed attention, which RKNN doesn't support natively. Current workarounds:
- **LCM Dreamshaper:** pre-encodes text on CPU, runs UNet + VAE on NPU
- **Model Zoo:** same split — CPU for attention, NPU for convolutions
- **rkllama:** delegates to internal handler which manages the split

This means the full pipeline is: CPU (text encode) → NPU (denoise loop) → NPU (VAE decode) → CPU (output). Not pure NPU, but the expensive part (denoising) runs on NPU.

## Enhancement Proposals

### Proposal 1: Image Generation Page in TinyAgentOS

**What:** Add an `/images` page to TinyAgentOS that provides a web UI for image generation using the existing rkllama endpoint.

**How it works:**
```
User enters prompt in TinyAgentOS web UI
  → TinyAgentOS POSTs to rkllama /v1/images/generations
  → rkllama runs SD on NPU
  → Returns base64 image
  → TinyAgentOS displays in browser + saves to gallery
```

**Features:**
- Prompt input with optional negative prompt
- Model selector (from installed image models)
- Size selector (256x256, 384x384, 512x512)
- Steps slider (1-8, default 4 for LCM)
- Seed input (random or specific)
- Generation history / gallery
- Download button per image

**Implementation:** New route `tinyagentos/routes/images.py`, template `images.html`. Uses httpx to call rkllama. Images stored in `data/images/` with metadata JSON.

**Effort:** Medium — the backend (rkllama) already works. This is purely UI + gallery storage.

### Proposal 2: RKNN Model Browser & Downloader

**What:** Extend the Model Manager page to support RKNN image models alongside LLM models. Users can browse available SD models converted for RKNN, download them, and they're automatically available in rkllama.

**Current model management pain:**
1. Find a compatible RKNN model on HuggingFace (which ones work? who knows)
2. Download manually via wget
3. Place in the right directory
4. Configure rkllama to use it
5. Hope the RKNN version matches your toolkit version

**Proposed flow:**
1. Open TinyAgentOS Model Manager
2. Filter by type: "Image Generation"
3. See available models with compatibility badges
4. Click "Download" — model downloads to correct location
5. Model appears in rkllama's model list automatically

**Implementation:** Add image model manifests to the catalog (we've started this). The download installer already handles file downloads with SHA256 verification. Need to add the rkllama model directory as a target path.

**Effort:** Low — mostly catalog manifests + a path configuration.

### Proposal 3: Remote Conversion Pipeline

**What:** RKNN model conversion requires x86 with the RKNN-Toolkit2. Most users don't have an x86 machine handy. Provide a conversion service.

**Options:**
- **A) Use the Fedora workstation** — Jay's Fedora box (77GB RAM, RTX 3060) has RKNN-Toolkit v1.2.3 installed. TinyAgentOS could queue conversion jobs and send them to the Fedora box.
- **B) Pre-convert and host** — convert popular models ourselves and host them on HuggingFace. Users just download pre-converted RKNN files.
- **C) Cloud conversion service** — future tinyagentos.com feature. Users upload a model, it converts on our server, they download the RKNN file.

**Recommendation:** B for now (we're already doing this with the Qwen3 models). A is useful for custom models. C is a future paid feature.

**Effort:** B is just uploading files to HuggingFace. A would be a new "conversion worker" service.

### Proposal 4: Unified Image Generation Backend

**What:** Instead of 4 separate projects that don't talk to each other, create a unified backend that:
- Exposes an OpenAI-compatible `/v1/images/generations` API (rkllama already does this)
- Supports multiple backends: RKNN NPU, Mali GPU (MLC), CPU (stable-diffusion.cpp)
- Manages models (download, convert, select)
- Handles the CPU↔NPU split transparently

**Why:** rkllama already does most of this. The gap is:
- Adding the Mali GPU backend (for boards where NPU is busy with LLMs)
- Adding CPU fallback via stable-diffusion.cpp
- Better model management (currently hardcoded model paths)

**This is essentially what rkllama + TinyAgentOS together provide.** The enhancement is making rkllama's image generation more robust and adding the web UI in TinyAgentOS.

**Effort:** Medium-high. The rkllama endpoint exists but has open issues (#76 image generation fails). Would need debugging + the GPU/CPU fallback backends.

### Proposal 5: Agent Image Generation Tool

**What:** Expose image generation as a tool that agents can call. An agent could generate illustrations for blog posts, create visual content for social media, or produce diagrams on demand.

**Implementation:** MCP tool definition that wraps the rkllama image generation API:
```json
{
  "name": "generate_image",
  "description": "Generate an image from a text prompt using Stable Diffusion on the local NPU",
  "parameters": {
    "prompt": "string",
    "size": "256x256 | 384x384 | 512x512",
    "style": "realistic | anime | artistic"
  }
}
```

**Effort:** Low — once the API works, wrapping it as an MCP tool is trivial.

## Priority Order

1. **Proposal 2: Model Browser** (Low effort, high value) — users can actually find and download image models
2. **Proposal 1: Web UI** (Medium effort, high value) — the "app store experience" for image generation
3. **Proposal 5: Agent Tool** (Low effort, medium value) — agents can generate images
4. **Proposal 3: Pre-converted models** (Low effort, medium value) — just upload to HuggingFace
5. **Proposal 4: Unified backend** (High effort, long-term value) — the proper architecture

## Key Repositories to Watch/Contribute

- **[airockchip/rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo)** — official Rockchip. Contributing a web UI or better deployment scripts here would have maximum impact.
- **[darkautism/LCM-Dreamshaper-V7-rs](https://github.com/darkautism/LCM-Dreamshaper-V7-rs)** — fastest implementation. Contributing multi-model support or an HTTP API would make it immediately useful.
- **[NotPunchnox/rkllama](https://github.com/NotPunchnox/rkllama)** — already has the API. Our fork could improve the image generation reliability (issue #76) and add model management.
- **[Pelochus/ezrknpu](https://github.com/Pelochus/ezrknpu)** — easy installation wrapper. Could integrate with TinyAgentOS's installer.

## Sources

- https://github.com/airockchip/rknn_model_zoo
- https://github.com/darkautism/LCM-Dreamshaper-V7-rs
- https://github.com/happyme531/RK3588-stable-diffusion-GPU
- https://github.com/NotPunchnox/rkllama
- https://github.com/Pelochus/ezrknpu
- https://github.com/rockchip-linux/rknn-toolkit2
- https://docs.radxa.com/en/rock5/rock5b/app-development/ai/rknn-stable-diffusion
- https://tinycomputers.io/posts/rockchip-npu-benchmarks.html
- https://huggingface.co/happyme531/Stable-Diffusion-1.5-LCM-ONNX-RKNN2
