# Raspberry Pi 5

## Hardware

- **SoC:** BCM2712 (Cortex-A76, 4 cores, 2.4GHz)
- **RAM:** 4 / 8 / 16 GB LPDDR4X
- **NPU:** None built-in
- **GPU:** VideoCore VII (not useful for LLM inference)
- **Accelerators:** M.2 HAT+ (Hailo-8L 13 TOPS, Hailo-10H 40 TOPS), USB (Coral)
- **Storage:** microSD, NVMe via M.2 HAT+

## TinyAgentOS Profiles

| Config | Profile |
|--------|---------|
| Pi 5, 8GB, no accelerator | `arm-cpu-8gb` |
| Pi 5, 16GB, no accelerator | `arm-cpu-16gb` |
| Pi 5 + Hailo-8L (AI Kit, 13 TOPS) | `arm-hailo-8gb` or `arm-hailo-16gb` |
| Pi 5 + Hailo-10H (AI HAT+ 2, 40 TOPS) | `arm-hailo10h-8gb` or `arm-hailo10h-16gb` |

## Model Strategy

**Without accelerator (CPU only):**
- Embedding: embeddinggemma-300M via llama.cpp — fast enough on A76 cores
- Chat: Qwen3-4B (Q4_K_M, 2.5GB) on 8GB, Qwen3-8B on 16GB — 4-6 tok/s
- Reranking: Qwen3-Reranker-0.6B via llama.cpp — slower than NPU but works
- Much better than Pi 4 due to A76 cores (2x faster per core)

**With Hailo-8L (AI Kit, 13 TOPS):**
- Vision/object detection: fully supported
- LLM inference: NOT supported (Hailo-8L can't run LLMs)
- Still useful for camera-based agents, security, object detection

**With Hailo-10H (AI HAT+ 2, 40 TOPS) — NEW 2026:**
- LLM inference: YES — can accelerate LLMs and VLMs
- 40 TOPS INT4 performance
- Recommended for serious Pi-based agent deployments
- Requires hailo-genai package for LLM support

## Accelerator Options

| Accelerator | Interface | TOPS | LLM Support | Price | Notes |
|-------------|-----------|------|-------------|-------|-------|
| **Hailo-10H (AI HAT+ 2)** | M.2 HAT+ | 40 | **Yes** | ~$110 | LLMs + VLMs, recommended for Pi |
| **M5Stack LLM-8850** | M.2 + HAT | 24 | **Yes** | ~$140 | Axera AX8850, 8GB dedicated RAM, also works on RK3588 |
| Hailo-8L (AI Kit) | M.2 HAT+ | 13 | No | ~$70 | Vision/object detection only |
| Google Coral USB | USB 3.0 | 4 | No | ~$40 | Vision/classification only |
| Coral M.2 Dual | M.2 HAT+ | 8 | No | ~$40 | Dual TPU, vision only |

## Build Command

```bash
# Using Armbian
./build.sh rpi5 BRANCH=current

# Or stock Raspberry Pi OS with install script
curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/install.sh | sudo bash
```

## Kernel

Use **current** branch (Armbian mainline-based). No vendor kernel needed — mainline support is excellent. For Hailo support, Raspberry Pi OS may be better (native Hailo driver support).

## Notes

- 8GB minimum, 16GB recommended for running agents + models
- NVMe via M.2 HAT+ strongly recommended (conflicts with AI HAT+ — can't use both M.2 slots)
- If using Hailo-10H for LLM inference AND want NVMe, use USB SSD instead
- Pi 5 is the most accessible SBC — huge community, easy to buy
- Best Pi experience: 16GB + Hailo-10H + USB SSD
- Most widely available SBC, good for development and testing
