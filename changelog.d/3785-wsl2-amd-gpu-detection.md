### Fixed

- Hardware: AMD GPUs under WSL2 are now detected via `/dev/dxg` + `rocminfo`. WSL2 exposes the GPU through DXG (no lspci, no `/opt/rocm`), so workers running there previously reported no leasable GPU resource. `rocminfo` is parsed per agent block and the `Device Type: GPU` block is selected before its marketing name is read, so a CPU agent listed first is no longer reported as the GPU. VRAM is looked up from the existing AMD table. Vulkan is no longer assumed from a ROCm hit; it is reported only when the independent capability check passes (#3228).
