### Changed

- MLX serving is no longer one model at a time. `mlx_lm.server` serves exactly
  one model per process, so every installed model is now pinned in **its own**
  launchd agent — `~/Library/LaunchAgents/com.taos.mlx-server-<app_id>.plist`,
  its label derived from the model directory's name — instead of the single
  `com.taos.mlx-server` agent. Installing model B no longer re-points (and
  silently stops) model A; each model keeps a server of its own.
- The first (or only) MLX model keeps the reserved default port `7837`, so the
  onboarding checklist's Metal probe (which does not know which models are
  installed) still finds the backend. Every further model is pinned to a
  deterministic, non-reserved free port from
  `port_allocator.allocate_host_port()`; a model whose agent already exists keeps
  the port written in its plist, so a re-install does not move it.
- `scripts/install-mlx-server.sh` accepts the reserved default as a preferred
  `--port` and writes one plist per model, and `--uninstall --model <dir>`
  unloads only the agents that pin that model (its per-model label, or a legacy
  `com.taos.mlx-server` plist an upgrade left behind) and leaves every other
  model's agent running. Installing a model retires the legacy single agent when
  it pins that same model, freeing `7837`; an agent serving a different model is
  never touched.
