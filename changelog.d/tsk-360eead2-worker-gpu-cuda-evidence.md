### Fixed

- A Linux worker no longer advertises `gpu-cuda-0` just because a GPU-capable
  backend (`vllm`, `ollama`, `exo`, `mlx`) is running. A GPU class now comes
  from the hardware probe alone, with no backend needed (#3228): `gpu.type`
  plus the probe's usability flag -- `nvidia`/`cuda` with `GpuInfo.cuda` ->
  `gpu-cuda-0`, `amd`/`rocm` with `GpuInfo.rocm` -> `gpu-rocm-0`, Apple
  Silicon -> `gpu-metal`. So a CPU-only host running Ollama in CPU mode (or an
  NVIDIA card whose driver did not load) stops claiming a CUDA device the
  controller, the scheduler and A2A lease callers would otherwise route
  CUDA-typed work to. The live-backend filter applies to `npu-rk3588` only:
  manifest-synthetic `status: "stopped"` backend entries (declared software
  that is not answering) no longer count, so a Pi advertises only
  `cpu-inference` while rkllama restarts. (taOS #37 follow-up)
