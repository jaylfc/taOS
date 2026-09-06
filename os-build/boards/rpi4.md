# Raspberry Pi 4 (8GB)

## Hardware

- **SoC:** BCM2711 (Cortex-A72, 4 cores, 1.8GHz)
- **RAM:** 8GB (minimum for TinyAgentOS)
- **NPU:** None built-in
- **GPU:** VideoCore VI (not useful for LLM inference)
- **Accelerators:** USB (Coral), GPIO HATs
- **Storage:** microSD, USB 3.0 SSD recommended

## TinyAgentOS Profile

`arm-cpu-8gb` — CPU-only inference, smallest quantized models.

## Model Strategy

- **Embedding:** embeddinggemma-300M (Q8_0, ~300MB) via llama.cpp — fits comfortably
- **Chat:** TinyLlama 1.1B (Q4_K_M) — 8-12 tok/s, usable for simple agents
- **Chat:** Qwen3-1.7B (Q4_K_M, 1.1GB) — 2-4 tok/s, slower but more capable
- **Reranking:** Skip or use smallest model — too slow for real-time on CPU
- **Query expansion:** Skip — use keyword search only

Realistic performance: 2-3 tok/s for 7B Q4, 8-12 tok/s for 1B models.

## Accelerator Options

| Accelerator | Interface | TOPS | LLM Support | Notes |
|-------------|-----------|------|-------------|-------|
| Google Coral USB | USB 3.0 | 4 | No | Vision/classification only |
| Coral M.2 (via HAT) | M.2 | 4 | No | Same, M.2 form factor |

No LLM-capable accelerators available for Pi 4.

## Build Command

```bash
# Using Raspberry Pi OS (not Armbian — better Pi support)
# TinyAgentOS installed via install script on stock Raspberry Pi OS

curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/install.sh | sudo bash
```

## Notes

- Pi 4 is the minimum viable hardware — functional but slow for LLMs
- USB SSD strongly recommended over microSD for model storage and SQLite performance
- 4GB Pi 4 is NOT supported (insufficient RAM for models + OS + TinyAgentOS)
- Best used as an agent host running lightweight frameworks (picoclaw, PocketFlow) with remote inference backend
- Consider pointing at a more powerful machine for inference via multi-host fallback
