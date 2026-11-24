### Added

- The MLX backend now serves the model it installs. `MLXInstaller.install()`
  pins the model in a per-user launchd agent
  (`~/Library/LaunchAgents/com.taos.mlx-server-<app_id>.plist`, written by
  `scripts/install-mlx-server.sh`) that runs `<mlx-venv>/bin/mlx_lm.server
  --model <model dir> --host 127.0.0.1 --port <port>`, health-gates on
  `GET /v1/models` and only then reports `endpoint` / `runtime_location` in the
  install result. An install whose server does not come up keeps the downloaded
  weights, reports no endpoint at all, and carries `mlx_serving_error` saying
  why. `mlx_lm.server` serves one model per process, so each installed model
  gets its own agent and port and several MLX models are served at once.
- The onboarding checklist's Apple Silicon step is satisfied by either local
  backend: `accel == "metal"` now probes llama.cpp (`/health`) or the MLX
  server (`/v1/models`), so a Mac that installed only an MLX model no longer
  reads as "default backend not running".

### Changed

- `MLXInstaller.uninstall()` unloads that launchd agent when it was serving the
  removed model; otherwise launchd's `KeepAlive` would restart a server against
  a model directory that no longer exists. Uninstalling any other model leaves
  the agent alone.
- `mlx_server_is_running()` takes an optional `port` (default unchanged:
  `TAOS_MLX_PORT`, else 7837) so the installer probes the port it pinned the
  agent to, and its docstring records that `mlx_lm.server` 0.31.3 also answers
  `/health` while the probe deliberately checks the OpenAI surface taOS calls.
- The MLX agent's plist values are XML-escaped, so a model directory such as
  `Models & Data` produces a plist launchd can load, and `--uninstall` compares
  against the same escaped directory; the model path pinned in the agent is the
  resolved one uninstall later matches.
- `MLXInstaller.uninstall()` reports `mlx_agent_state`
  (`unloaded` / `left-running` / `failed`) and only sets `mlx_agent_unloaded`
  when the agent really was unloaded, so an agent deliberately left serving a
  different model is not reported as stopped. `install-mlx-server.sh
  --uninstall` confirms the bootout with `launchctl print` (only launchctl's
  unknown-service answer counts as absence) and checks the plist removal, so a
  still-registered agent, an inspection that cannot answer, or a plist that
  could not be removed is a failure rather than a success.
