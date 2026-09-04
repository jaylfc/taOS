### Fixed

- VNC password: replaced hardcoded 'testpass' with a per-start random password generated via `secrets.token_urlsafe`, passed to the container as an argv element instead of being interpolated into the `bash -c` script
- Owner access: the four `/api/agents/{agent_name}/desktop/*` handlers now resolve the agent through the registry and enforce owner-or-admin (403 otherwise) before deriving a container name, touching state, or returning the VNC password; a name with no registry row is administrator-only, and a name that is not a valid container slug is rejected with 400
- Desktop start readiness: the start probe watches the PIDs it launched and connects to port 5900 rather than matching `pgrep -f x11vnc`, which also matched the wrapper shell, and reports `state = "running"` only once the VNC server accepts a connection
- Install retry: installation completion is tracked on its own flag, so a transient apt failure no longer permanently skips installation on every later request; `start` is rejected with 409 while installation has not completed, and an install call on an already-installed desktop clears an error left behind by a later start or stop instead of answering 200 with that error
- Lifecycle serialization: install, start, stop and status hold a per-agent lock across their state updates and container commands, so concurrent installs cannot both run apt and a stop cannot complete underneath an in-flight start
- Stop failures: a non-zero result from the stop command records `error` and returns 500 instead of reporting `stopped` while desktop processes are still running
- Docs: fixed literal `\n` sequences in `docs/routes.d/14-agent-desktop.md` and `docs/routes.md` that collapsed the agent-desktop section onto one physical line
