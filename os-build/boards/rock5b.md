# Rock 5B (Radxa)

| Field | Value |
|-------|-------|
| SoC | Rockchip RK3588 |
| RAM | 4 / 8 / 16 GB LPDDR4X |
| NPU | 6 TOPS RKNN (integrated) |
| GPU | Mali-G610 MP4 |
| Storage | eMMC, NVMe M.2, microSD |
| Ethernet | 1x 2.5GbE |

## Kernel

Use **vendor** branch for rkllama, which requires the vendor RKNPU2 runtime.
A mainline RK3588 NPU driver (rocket) was merged in 2025-07-28 (source:
blog.tomeuvizoso.net), but uses a different userspace stack (Mesa Teflon).
Whether rkllama runs on the mainline driver is currently unverified.

## Build Command

```bash
./build.sh rock-5b
```

Or manually:

```bash
./compile.sh BOARD=rock-5b BRANCH=vendor RELEASE=bookworm BUILD_DESKTOP=no \
  ENABLE_EXTENSIONS=tinyagentos
```

## NPU Support

Full RKNN support — same RK3588 SoC as Orange Pi 5 Plus.

## Board-Specific Notes

- Good alternative if Orange Pi boards are unavailable.
- NVMe M.2 2280 slot for fast storage.
- Has HDMI input which is unusual but not relevant for TinyAgentOS.
