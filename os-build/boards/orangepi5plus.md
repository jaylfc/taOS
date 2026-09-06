# Orange Pi 5 Plus

**Primary target board for TinyAgentOS.**

| Field | Value |
|-------|-------|
| SoC | Rockchip RK3588 |
| RAM | 8 / 16 / 32 GB LPDDR4X |
| NPU | 6 TOPS RKNN (integrated) |
| GPU | Mali-G610 MP4 |
| Storage | eMMC, NVMe M.2, microSD |
| Ethernet | 2x 2.5GbE |

## Kernel

Use **vendor** branch for full NPU and GPU support via the Rockchip BSP kernel.
Mainline kernel lacks RKNN NPU drivers.

## Build Command

```bash
./build.sh orangepi5plus
```

Or manually:

```bash
./compile.sh BOARD=orangepi5-plus BRANCH=vendor RELEASE=bookworm BUILD_DESKTOP=no \
  ENABLE_EXTENSIONS=tinyagentos
```

## NPU Support

The RKNN NPU is fully supported via the vendor kernel. The `rknn-toolkit2` and
`rkllama` runtime are installed by TinyAgentOS for local model inference.

## Board-Specific Notes

- Dual 2.5GbE makes this ideal for running multiple LXC agent containers.
- NVMe slot recommended for model storage and fast container I/O.
- 16 GB or 32 GB RAM variant strongly recommended for agent workloads.
