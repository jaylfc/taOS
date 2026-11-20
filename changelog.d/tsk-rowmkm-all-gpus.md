### Fixed
- Activity now reports all GPUs (NVIDIA, AMD, Intel, Mali/ARM) instead of just the first one. The /api/activity endpoint returns a gpus array with one entry per GPU in PCI order, while keeping the legacy gpu field for compatibility. The Activity app renders one card per GPU.
