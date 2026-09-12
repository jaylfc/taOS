# Orange Pi 5

| Field | Value |
|-------|-------|
| SoC | Rockchip RK3588S |
| RAM | 4 / 8 / 16 GB LPDDR4X |
| NPU | 6 TOPS RKNN (integrated) |
| GPU | Mali-G610 MP4 |
| Storage | eMMC, NVMe M.2, microSD |
| Ethernet | 1x GbE |

## Kernel

Use **vendor** branch. Same rationale as Orange Pi 5 Plus — rkllama requires
the vendor RKNPU2 runtime. A mainline RK3588 NPU driver (rocket) exists as of
2025-07-28 (source: blog.tomeuvizoso.net), but uses a different userspace stack
(Mesa Teflon). Whether rkllama runs on the mainline driver remains an open
question.

## Build Command

```bash
./build.sh orangepi5
```

Or manually:

```bash
./compile.sh BOARD=orangepi5 BRANCH=vendor RELEASE=bookworm BUILD_DESKTOP=no \
  ENABLE_EXTENSIONS=tinyagentos
```

## NPU Support

Same RKNN NPU as the 5 Plus (RK3588S is a reduced-IO variant of RK3588, NPU is
identical).

## Board-Specific Notes

- RK3588S has fewer PCIe lanes than RK3588 — no dual ethernet.
- Single GbE is sufficient for most deployments.
- 16 GB variant recommended for agent workloads.
