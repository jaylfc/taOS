### Fixed

- A Linux worker no longer advertises `gpu-cuda-0` just because a GPU-capable
  backend (`vllm`, `ollama`, `exo`, `mlx`) is running. The class is now
  advertised only when a *live* backend is serving and the hardware probe
  reports a usable CUDA/ROCm runtime (`GpuInfo.cuda` / `GpuInfo.rocm`), so a
  CPU-only host running Ollama in CPU mode (or an NVIDIA card whose driver did
  not load) stops claiming a CUDA device the controller, the scheduler and A2A
  lease callers would otherwise route CUDA-typed work to. Manifest-synthetic
  `status: "stopped"` backend entries (declared software that is not answering)
  no longer count as evidence either, for `npu-rk3588` as well as `gpu-cuda-0`.
  (taOS #37 follow-up)
