### Added

- **macOS worker install registers Apple Silicon as a `gpu-metal` resource**
  (#37). `scripts/install-worker.sh` now runs a macOS accelerator probe in its
  Darwin branch: arm64 with Metal present selects the `gpu-metal` resource
  class (MLX / llama.cpp Metal / Core ML on unified memory, 1 concurrent task),
  and probes the python for the MLX runtime only to advise on GPU inference.
  Intel Macs, and arm64 hosts whose Metal probe answers nothing (a VM), fall
  back to `cpu-inference`. `TAOS_FORCE_METAL=1` forces the Apple Silicon branch
  on a bench box whose probe is silent; `TAOS_WORKER_RESOURCES` overrides the
  detected set.

### Fixed

- The macOS install now pairs the worker before it installs the launchd
  agent. The pair step used to live inside `install_and_enroll_incus()`, which
  the Darwin branch skips (incus is Linux-only), so a Mac started a launchd
  agent with no signing key and reported "not paired" forever (#37). The step
  is now the shared `pair_worker()` function, called from the incus path on
  Linux and directly before `install_macos_launchd` on macOS, always with
  `--state-dir $INSTALL_DIR/.taos-worker-state`.
- The "not paired" hint in `tinyagentos.worker.agent` now includes
  `--state-dir`, so following it writes the key where the service reads it
  instead of pairing.py's `~/.local/state/taos-worker` default.
- The controller validates a worker's advertised `resources` against the
  scheduler resource-class grammar (`tinyagentos/cluster/worker_protocol.py`)
  on registration and heartbeat, and answers 400 for an unknown class. The
  list used to be stored verbatim, and `TAOS_WORKER_RESOURCES` makes it an
  operator knob. The lease-time fallback grammar in `ClusterManager` now
  accepts `gpu-metal` too.

### Changed

- The macOS launchd agent (`~/Library/LaunchAgents/com.tinyagentos.worker.plist`)
  now carries `EnvironmentVariables` (`PYTHONUNBUFFERED`,
  `TAOS_WORKER_STATE_DIR`, and the detected `TAOS_WORKER_RESOURCES`), so the
  resource class survives a re-login instead of depending on the installer's
  process environment.
- `tinyagentos.worker.agent` advertises Apple Silicon as `gpu-metal` rather
  than `gpu-cuda-0` (an MLX/ollama backend on a Mac is the Metal GPU, not a
  CUDA device), and unions the installer-detected classes from
  `TAOS_WORKER_RESOURCES` into the `resources` array it reports at
  registration and on every heartbeat.
- On a Mac that is not Apple Silicon, a running Ollama/llama.cpp backend no
  longer makes the worker advertise `gpu-cuda-0`: macOS has no CUDA or ROCm
  class, so an Intel Mac stays `cpu-inference` in both its registration and
  its heartbeats, matching the installer's fallback.
