# Catalog Platform Status

Cross-platform verification status for every app, model, and backend in `app-catalog/`.
Each PR that adds, removes, or verifies an entry updates this doc in the same commit.
The roadmap of what's planned lives in [#321](https://github.com/jaylfc/tinyagentos/issues/321);
this doc records what's actually verified working.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | **Tested** — full smoke test on real hardware on this tier |
| 🔧 | **Wired** — install path implemented; not tested on this tier yet |
| ⏳ | **Pending** — manifest exists; no install path tested anywhere |
| ⚠️ | **Partial** — install works but inference flaky / known issues |
| ❌ | **N/A** — not supported on this hardware (architecture mismatch, too big, etc) |
| 🚫 | **Blocked** — known broken; link to issue |

## Hardware tiers

| Tier | Description | Example device |
|---|---|---|
| **Pi-NPU-16GB** | Rockchip RK3588 with 16GB unified memory + NPU | Orange Pi 5 Plus 16GB |
| **Pi-NPU-32GB** | RK3588 with 32GB | Orange Pi 5 Max |
| **Mac-MLX** | Apple Silicon (M1/M2/M3/M4) with MLX-served models | MacBook Pro M-series |
| **Linux-x86-GPU** | Linux x86 with discrete NVIDIA GPU | RTX 3060 Fedora dev box |
| **Linux-x86-CPU** | Linux x86, CPU-only | controller fallback |
| **Win-WSL** | Windows + WSL2 | Win11 dev box |

## LLM backends

| Backend | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Win-WSL | Notes |
|---|---|---|---|---|---|---|---|
| `rkllama` | ✅ | 🔧 | ❌ | ❌ | ❌ | ❌ | install-rknpu.sh ships it; issue #318 cycle stable |
| `rk-llama.cpp` | ✅ | 🔧 | ❌ | ❌ | ❌ | ❌ | scripts/install-rk-llama-cpp.sh + RkLlamaCppInstaller wired; pinned-SHA tarball; 288 MiB on RKNPU verified |
| `ollama` | 🔧 | 🔧 | 🔧 | 🔧 | 🔧 | 🔧 | catalog entry; install path not yet wired |
| `llama-cpp` | 🔧 | 🔧 | 🔧 | 🔧 | 🔧 | 🔧 | catalog entry; install path not yet wired |
| `vllm` | ❌ | ❌ | ❌ | 🔧 | ❌ | 🔧 | x86 GPU only; not yet wired |
| `mlx` | ❌ | ❌ | ⏳ | ❌ | ❌ | ❌ | declared in tiers; no apps point at it yet |
| `mlc-llm` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | not in catalog yet (Pack 9) |

## LLM models — rkllm format (Pi NPU)

Catalog entries with `format: rkllm` + `backend: [rkllama]`. PR #320 wired the rkllama installer.

| Model | Pi-NPU-16GB | Pi-NPU-32GB | Source | Notes |
|---|---|---|---|---|
| `pelochus-qwen-1.8b-rkllm` | ⏳ | ⏳ | Pelochus/qwen-1_8B-rk3588 | Older Qwen 1.8B |
| `qwen2.5-1.5b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-1.5B-Instruct-rk3588-1.1.1 | |
| `qwen2.5-3b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-3B-Instruct-rk3588-1.1.1 | |
| `qwen2.5-7b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-7B-Instruct-rk3588-v1.1.0 | Tight on 16GB |
| `qwen2.5-14b-rkllm` | ❌ | ⏳ | c01zaut/Qwen2.5-14B-Instruct-rk3588-1.1.1 | 32GB only |
| `qwen2.5-coder-1.5b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-Coder-1.5B-Instruct-RK3588-1.1.4 | |
| `qwen2.5-coder-7b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-Coder-7B-Instruct-rk3588-1.1.2 | |
| `qwen2.5-coder-14b-rkllm` | ❌ | ⏳ | c01zaut/Qwen2.5-Coder-14B-Instruct-RK3588-1.1.4 | 32GB only, 15.6 GB |
| `qwen2.5-math-1.5b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-Math-1.5B-Instruct-RK3588-1.1.4 | |
| `qwen2.5-math-7b-rkllm` | ⏳ | ⏳ | c01zaut/Qwen2.5-Math-7B-Instruct-RK3588-1.1.4 | |
| `qwen3-1.7b-rkllm` | ✅ | 🔧 | GatekeeperZA/Qwen3-1.7B-RKLLM-v1.2.3 | E2E pull verified PR #320 |
| `qwen3-4b-rkllm` | ⏳ | ⏳ | thanhtantran/Qwen3-4B-Instruct-2507-RKLLM | |
| `qwen3-vl-2b-rkllm` | ⏳ | ⏳ | GatekeeperZA/Qwen3-VL-2B-Instruct-RKLLM-v1.2.3 | Vision; serving path TBD |
| `qwen3-vl-4b-rkllm` | ⏳ | ⏳ | reponislam/Qwen3-VL-4B-Instruct-w8a8-RK3588-rkllm | Vision; serving path TBD |

Pre-loaded by `install-rknpu.sh` (separate from Store install path):

| Model | Pi-NPU-16GB | Pi-NPU-32GB | Notes |
|---|---|---|---|
| `qwen3-embedding-0.6b` | ✅ | ✅ | embedded in rkllama default load |
| `qwen3-reranker-0.6b` | ✅ | ✅ | embedded in rkllama default load |
| `qmd-query-expansion` | ✅ | ✅ | embedded in rkllama default load |

## LLM models — GGUF format (rk-llama.cpp / Ollama / llama.cpp)

GGUF-format models route through the resolver's `requires.backends` list — manifests pick `rk-llama-cpp` for Pi NPU and fall back to `ollama` / `llama-cpp` on other tiers.

| Model | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Source | Notes |
|---|---|---|---|---|---|---|---|
| `qwen3-4b` (GGUF) | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | Qwen/Qwen3-4B-GGUF | catalog has it; backends in `requires` |
| `gemma-4-e2b-gguf` | ✅ | ✅ | ⏳ | ⏳ | ⏳ | unsloth/gemma-4-E2B-it-GGUF | first GGUF on rk-llama.cpp |
| Gemma 4 E4B (GGUF) | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | (followup) | follows e2b shape |
| Qwen 3.5 2B (GGUF) | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | (followup) | rk-llama.cpp + ollama target |
| Qwen 3.5 9B (GGUF) | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | (followup) | rk-llama.cpp + ollama target |

## Vision-language models

| Model | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `florence-2-base` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | catalog entry only |
| `qwen2-vl-7b` | ❌ | ⏳ | ⏳ | ⏳ | ❌ | |
| `llava-phi-3-mini` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | |
| `qwen3-vl-2b-rkllm` | ⏳ | ⏳ | ❌ | ❌ | ❌ | NPU-only; rkllama serving path TBD |
| `qwen3-vl-4b-rkllm` | ⏳ | ⏳ | ❌ | ❌ | ❌ | NPU-only; rkllama serving path TBD |
| `moondream2` | — | — | — | — | — | (Pack 5) not yet in catalog |
| `paligemma-2` | — | — | — | — | — | (Pack 5) not yet in catalog |
| `smolvlm` | — | — | — | — | — | (Pack 5) not yet in catalog |

## Speech-to-text

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `whisper-stt` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | catalog entry only |
| `faster-whisper` | — | — | — | — | — | (Pack 3) not yet in catalog |
| `whisperx` | — | — | — | — | — | (Pack 3) not yet in catalog |
| `distil-whisper` | — | — | — | — | — | (Pack 3) not yet in catalog |
| `parakeet-nemo` | — | — | — | — | — | (Pack 3) not yet in catalog |
| `sense-voice` | — | — | — | — | — | (Pack 3) not yet in catalog |

## Text-to-speech

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `kokoro-tts` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | catalog entry only |
| `piper` (backend) | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | backend declared, no models yet |
| `coqui-tts` | — | — | — | — | — | (Pack 4) not yet in catalog |
| `style-tts-2` | — | — | — | — | — | (Pack 4) not yet in catalog |
| `f5-tts` | — | — | — | — | — | (Pack 4) voice cloning |
| `openvoice` | — | — | — | — | — | (Pack 4) voice cloning |
| `bark` | — | — | — | — | — | (Pack 4) |

## Image generation

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `comfyui` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | catalog entry only |
| `fooocus` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | |
| `rknn-stable-diffusion` | ⏳ | ⏳ | ❌ | ❌ | ❌ | NPU only |
| `stable-diffusion-cpp` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | |
| `fastsdcpu` | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | |

## Video generation

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `animatediff` | ❌ | ❌ | ⏳ | ⏳ | ❌ | catalog entry only |
| `corridorkey` | ❌ | ❌ | ⏳ | ⏳ | ❌ | catalog entry only |
| `wan-2.1` | — | — | — | — | — | (Pack 8) not in catalog |
| `hunyuanvideo` | — | — | — | — | — | (Pack 8) |
| `ltx-video` | — | — | — | — | — | (Pack 8) |
| `cogvideox` | — | — | — | — | — | (Pack 8) |
| `mochi-1` | — | — | — | — | — | (Pack 8) |

## Music / audio gen

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `musicgen` | — | — | — | — | — | (Pack 7) |
| `stable-audio-open` | — | — | — | — | — | (Pack 7) |

## Document parsing / OCR

| App | Pi-NPU-16GB | Pi-NPU-32GB | Mac-MLX | Linux-x86-GPU | Linux-x86-CPU | Notes |
|---|---|---|---|---|---|---|
| `tesseract` | — | — | — | — | — | (Pack 2) |
| `paddleocr` | — | — | — | — | — | (Pack 2) |
| `docling` | — | — | — | — | — | (Pack 2) |
| `marker` | — | — | — | — | — | (Pack 2) |
| `surya` | — | — | — | — | — | (Pack 2) |
| `mineru` | — | — | — | — | — | (Pack 2) |

## Vector DBs

| App | Notes |
|---|---|
| `qdrant` | (Pack 2) docker service — not yet in catalog |
| `weaviate` | not yet in catalog |
| `pgvector` | postgres extension — not yet in catalog |

## Agent frameworks

| Framework | Status | Notes |
|---|---|---|
| `smolagents` | ⏳ | catalog entry; install path not tested |
| `pocketflow` | ⏳ | catalog entry |
| `openclaw` | ✅ | active in production on Pi |
| `langroid` | ⏳ | catalog entry |
| `openai-agents-sdk` | ⏳ | catalog entry |
| `crewai` | — | (Pack 10) |
| `langgraph` | — | (Pack 10) |
| `autogen` | — | (Pack 10) |
| `pydantic-ai` | — | (Pack 10) |

## Code agents

(Pack 11) — none in catalog yet.

| App | Notes |
|---|---|
| `aider` | |
| `open-interpreter` | |
| `goose` | Block's coding agent |
| `plandex` | |

## Workflow / automation

| App | Notes |
|---|---|
| `n8n` | ⏳ catalog entry only |
| `activepieces` | (Pack 12) not in catalog |

## Updating this doc

- Adding a row: include in the same PR that lands the manifest / installer.
- Promoting status (e.g., 🔧 → ✅): cite the PR or smoke-test session that did it. A 1-line "Notes" entry is enough.
- Demoting (✅ → ⚠️ / 🚫): always link the issue with the failure.
- A — entry means the app/model is on the roadmap but no manifest exists yet.

The truth source for "what's in the catalog right now" is `app-catalog/`. The truth source for "what's planned" is [#321](https://github.com/jaylfc/tinyagentos/issues/321). This doc is the truth source for "what's verified working on what".
