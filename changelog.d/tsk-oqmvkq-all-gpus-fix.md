### Fixed
- Intel GPUs now appear in correct PCI order alongside NVIDIA/AMD GPUs in the hardware detection and Activity app
- GPUs without runtime stats (Intel, Mali, Apple) now report `load: null` instead of an object with null fields; Activity app hides the Load bar for them
- NVIDIA GPU stats are now sorted by PCI bus ID (from nvidia-smi) instead of nvidia-smi index, ensuring consistent cross-vendor PCI ordering
- Fixed literal `\u00d7` in ActivityApp ZRAM display to render the actual multiplication sign (×)
- Added `/api/activity` route tests for the `gpus` array, Intel GPU `load: null` behavior, and PCI ordering
