# Apple Silicon (M1-M5)

## Hardware

- **SoCs:** M1, M1 Pro/Max/Ultra, M2, M2 Pro/Max/Ultra, M3, M3 Pro/Max, M4, M4 Pro/Max, M5, M5 Pro/Max/Ultra
- **RAM:** 8-192GB unified memory (acts as both RAM and VRAM)
- **GPU:** Integrated Apple GPU (MLX-accelerated)
- **NPU:** Apple Neural Engine (16-core, not currently used for LLM inference)
- **Machines:** Mac Mini, MacBook Air/Pro, Mac Studio, Mac Pro, iMac

## TinyAgentOS Profiles

| Config | Profile | Notes |
|--------|---------|-------|
| M1/M2 8GB | mac-mlx-8gb | Small models (4B), good for workers |
| M1/M2 16GB | mac-mlx-16gb | 7-8B models comfortably |
| M3/M4 Pro 18-36GB | mac-mlx-36gb | 14B models, training capable |
| M2/M3/M4 Max 64-96GB | mac-mlx-96gb | 70B models, serious workstation |
| M2/M4/M5 Ultra 128-192GB | mac-mlx-192gb | Multiple 70B models simultaneously |

## Inference Backends

| Backend | Speed | Notes |
|---------|-------|-------|
| **Ollama (MLX)** | 93% faster than pre-MLX | Recommended — native MLX since March 2026 |
| **llama.cpp (Metal)** | Fast | Metal GPU backend, well-tested |
| **MLX-LM** | Fastest | Direct MLX framework, Python API |
| **vLLM** | Good | Supports Metal backend |

## Model Strategy

Apple Silicon's unified memory is its superpower — ALL system RAM is available to the model. A Mac Mini with 24GB can run models that need 24GB VRAM, which on NVIDIA would require an RTX 3090.

| Unified RAM | Recommended Models |
|-------------|-------------------|
| 8GB | Qwen3-4B, Llama 3.2-3B |
| 16GB | Qwen3-8B, Llama 3-8B |
| 24-36GB | Qwen3-14B, Llama 3-14B |
| 64GB | Qwen3-32B, Llama 3-70B (Q4) |
| 96-128GB | Llama 3-70B (Q8), multiple models |
| 192GB | Llama 3-405B (Q4), anything |

## Installation

TinyAgentOS runs on macOS as a regular Python app or as a worker in a cluster:

```bash
# As controller (full dashboard)
pip install tinyagentos
tinyagentos

# As worker (joins another machine's cluster)
pip install tinyagentos[worker]
tinyagentos-worker http://your-server:6969
```

## Notes

- Ollama's MLX integration (March 2026) makes it the recommended backend
- M5 chips add Neural Accelerator support in MLX (4x speedup for prefill)
- Unified memory means no "VRAM limit" — the whole RAM pool is available
- Mac Mini M4 (24GB, ~$600) is a popular AI agent host
- Mac Studio / Mac Pro for serious multi-model deployments
- The TinyAgentOS worker tray app runs as a menu bar icon (no dock icon)
