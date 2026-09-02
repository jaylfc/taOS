### Fixed

- VNC password: replaced hardcoded 'testpass' with per-start random password generated via `secrets.token_urlsafe`
- Desktop start readiness: replaced fire-and-forget shell chain with bounded polling for x11vnc process liveness before reporting `state = "running"`
- Owner access: added `Depends(current_user)` session-user check to all four desktop handlers (`install_desktop`, `start_desktop`, `stop_desktop`, `desktop_status`)
- Docs: fixed literal `\n` sequences in `docs/routes.d/14-agent-desktop.md` and `docs/routes.md` that collapsed the agent-desktop section onto one physical line