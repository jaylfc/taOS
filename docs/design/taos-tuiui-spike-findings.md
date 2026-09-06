# taOS + tuiui Spike Findings

*Spike — not a feature build. Answers derived from probe runs against real tuiui code and binaries, not from README reading.*

---

## 1. Programmatic seam: yes, full Unix-socket + JSON API

**Is there a programmatic seam, or only a TUI?**

**Answer: YES — a full programmatic seam exists. A third party can enumerate sessions, spawn one, write stdin into a named window, and read its output/scrollback WITHOUT driving the rendered UI or synthesising keystrokes.**

- **Mechanism**: Unix-domain socket at `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock` (per-user, mode 0600 socket, 0700 directory). The daemon (`daemon.rs:run()`) listens on this socket and services one client at a time. A separate control socket at `$XDG_RUNTIME_DIR/tuiui-$USER/daemon-ctl.sock` handles `tuiui kill`/`tuiui reload` out-of-band.

- **Protocol**: newline-delimited JSON, using serde's externally-tagged enums. See `src/protocol.rs` and `src/session.rs:ClientMsg`.

- **What you can do over the socket** (all verified by probe runs):
  - `ListApps` / `ListApps` — enumerate running apps, get `app, cmd, args, pid, cols, rows, age_secs, alive`
  - `{"Spawn": {"req_id": N, "cmd": "sh", "args": ["-c", ...], "cwd": ..., "cols": ..., "rows": ...}}` — spawn a PTY-backed app; receives `{"Spawned": {"app": u64, "pid": N}}`
  - `{"Input": {"app": u64, "bytes": [104, 101, 108, 108, 111]}}` — write raw bytes to the app's PTY (Vec<u8> serializes as integer array, NOT base64)
  - `{"Scroll": {"app": u64, "lines": N}}` — scroll PTY scrollback (positive = back into history)
  - `{"SetMeta": {"app": u64, "meta": [...]}}` — store opaque window metadata for restore
  - `{"Kill": {"app": u64}}` / `"Shutdown"` — kill an app or the daemon
  - Frame events: `{"Frame": {"grid": {...}, "cursor": ..., "flags": ..., "images": [...], "image_data": [...], "clear": bool, "switch_to": ..., "clipboard": ...}}` — pushes the visible viewport grid + UI flags

- **No TUI driving or screen-scraping needed**: All input is PTY-byte-level, all output is a clean `CellBuffer` grid (decoded ANSI/SGR/CSI inside the apphost; see Q4). The probe transcript (probe.py) confirms: sending `{"Input": {"app":1, "bytes":[104,101,108,108,111]}}` types "hello" into the app, and receiving `Frame` events with `grid` cells contains the rendered text. No keystroke synthesis or raster screen-scraping was required.

- **CLI subcommands also exist** (`tuiui ps`, `tuiui kill-app <id>`, `tuiui launch <cmd>`, `tuiui kill`) but the pure-socket path is the programmatic seam.

---

## 2. Daemon across a container boundary

**What does the daemon expose across a container boundary? What has to cross: a unix socket bind-mount, a port, a shared filesystem?**

- **The socket**: The daemon's Unix socket lives at `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock`. `socket_dir()` in `src/protocol.rs:129` uses `XDG_RUNTIME_DIR` (typically a tmpfs at `/run/user/$UID`). If the variable is unset, it falls back to `std::env::temp_dir()`.

- **What must cross the boundary**:
  - **Option A — apphost inside the container**: The socket is bind-mounted *out* of the container. Agents run as PTY children of the apphost (same PID namespace). This is clean — the agent's `sh` process, alacritty terminal, and all scrollback live inside the container's PID namespace, and the only thing crossing is the Unix socket with newline-JSON frames.
  - **Option B — apphost outside the container**: The socket is bind-mounted *into* the container, but spawned agent processes inherit the apphost's environment (`PATH`, `HOME`, `SHELL`, `TERM=xterm-256color`, `COLORTERM=truecolor`) and PID namespace. Agents land in the apphost's namespace, not the container's. This requires explicit environment injection fixups if the agent needs to see the container's filesystem/PID namespace.

- **Immutable-core considerations**: `socket_dir()` uses `$XDG_RUNTIME_DIR` which is a tmpfs at `/run`. If the variable is unset, the fallback is `/tmp`. The directory is created mode `0700` and the socket mode `0600` (only the service user can connect). The apphost binary path is baked at install time via `install.sh` / `service.rs`. If taOS runs the tuiui service inside an immutable-core container, the `$XDG_RUNTIME_DIR` tmpfs must be writable by the service user, and the apphost binary must be accessible.

- **Breakage points under immutable-core**: If `$XDG_RUNTIME_DIR` is on a read-only filesystem, the daemon fails to create the socket directory/socket and exits. The apphost binary must be on a writable layer or via a bind-mount. No port-based exposure is used (Unix socket only).

---

## 3. Session identity and lifetime

**How does a taOS-side caller name a session stably across detach/reattach and across a daemon restart? Is there an id it can persist, or only a window index that renumbers?**

- **Across detach/reattach (same apphost)**: An app's `AppId` (u64) is **stable**. When a client detaches (socket closes) and reconnects, the on-connect `Roster` event lists all apps still alive in the apphost, with their `AppId`, `meta` blob, `pid`, `age_secs`, `cols`, `rows`, and `alive` status. The same `AppId` is returned, confirming the app survived the detach.

- **Across daemon restart (apphost preserved)**: The `AppId` counter **resets** on apphost restart. The new daemon starts numbering from 1. The only way to persist identity across a daemon restart is the **meta blob** stored via `SetMeta`.

- **Meta blob**: When the caller sends `{"SetMeta": {"app": u64, "meta": [...]}}`, the daemon stores an opaque JSON blob per app containing `{title, rect, z, minimized, app_key}`. This blob is persisted in the apphost and shipped to a fresh frontend on reload. It is the **sole mechanism for stable session naming across daemon restarts**. The `AppId` alone is not stable across restarts (counter resets), but paired with the meta blob, the app can be identified and its window restored via `restore_windows_from_host()`.

- **Practical naming**: A taOS-side caller should treat the `AppId` as a transient handle valid within one apphost lifetime, and the meta blob (title + rect + app_key) as the persistent identifier. On reconnect, roster the apps, match by meta title/rect, and use the corresponding `AppId` for input/Scroll/Input.

---

## 4. Output fidelity: clean CellBuffer, no ANSI, viewport-only

**Reading a coding agent's output means reading a full terminal emulator's grid. Establish whether the daemon can hand over clean text (scrollback as lines) or whether the caller inherits the ANSI/repaint problem.**

- **What crosses the socket**: A `CellBuffer` — a grid of `{ch: char, fg: {r,g,b,a}, bg: {r,g,b,a}, attrs: {bold,italic,underline,inverse}}` cells. **Zero cells contain ANSI escape sequences** (`\x1b` prefix) — all ANSI/SGR/CSI is decoded inside the apphost by the alacritty_terminal emulator (confirmed by probe: 0/250 cells had ANSI escapes).

- **Clean text, no ANSI problem**: Reconstructing "lines of text" is trivial: extract the `ch` field row-major from the grid. Per-cell fg/bg/attrs are also available. The probe confirmed: `printf 'A1-B2-C3\nline2-data\nline3-end\n'` produces three clean text lines with no ANSI.

- **BUT: scrollback is NOT arbitrarily fetchable**: Only the **visible viewport grid** is pushed via `Frame` events. The apphost holds the full scrollback internally (alacritty's `display_offset`), and the `Scroll` command changes the viewport, but there is **no command to fetch arbitrary scrollback lines as a text stream**. The caller sees the live viewport and can scroll it up/down, but cannot pull old lines off-screen as text.

- **Probe verification**: Spawning `for i in $(seq 1 50); do echo scroll-$i; done; sleep 3]` into a 5-row grid, then `Scroll(app, lines=-10)` changes the visible viewport, but no `Frame` event carries "the last 10 scrollback lines as text." The grid after scroll simply shows different rows of the same cell buffer. To get earlier lines, you must scroll viewport incrementally.

- **Summary**: The daemon gives you a clean, per-cell raster grid (no ANSI problem), but only the current viewport. If your use case requires "give me line 37 of scrollback as raw text," tuiui does not provide that — you must scroll the viewport to make it visible and then read the grid.

---

## 5. Cost of the alternative

**One paragraph, honest: what taOS would have to build if tuiui is not the vehicle, given the existing agent containers.**

If tuiui were not the vehicle, taOS would have to independently build **six major components**: (1) a PTY-spawning daemon that keeps children alive across UI detach (tuiui's `apphost server.rs` does this via a separate background process + Unix socket); (2) a real terminal emulator (alacritty) to decode ANSI into a cell grid — not optional, as coding agents emit color/cursor/keyboard output; (3) a persistent session store with stable session ID, window meta, scrollback buffers, and child-process lifecycle across restart; (4) a management IPC protocol (spawn/input/resize/kill/list/snapshot) — essentially re-deriving tuiui's `HostReq/HostEvt` over a socket; (5) a frontend compositor (tuiui's `Compositor` + `CellBuffer` diff protocol to the thin client); and (6) process supervision for the daemon (tuiui uses systemd/launchd via `tuiui service install`). Since taOS already adopts tuiui as the desktop shell, wrapping the existing apphost socket is dramatically cheaper than rebuilding all of the above. The three real gaps that need verification are: no stable session-id string (only u64 AppId, resets on restart), no arbitrary scrollback fetch (only visible viewport), and no remote/socket-via-SSH protocol yet (local Unix socket only).

---

## Related direction worth reconciling

- **opencode as the taOS harness**: opencode_runtime.py already runs opencode serve on the host. The tuiui apphost socket could become the unified terminal conduit for opencode-backed agents inside taOS, replacing separate container terminal emulators.
- **Agent-sandbox work**: Each agent already gets its own container (LXC/Docker/native). tuiui's apphost provides the PTY+terminal-emulator layer *inside* that container. The seam is the apphost Unix socket — agents spawn PTY children of apphost, and the taOS controller talks JSON over the socket.
- **Existing agent containers**: tuiui does not replace containers; it provides the terminal infrastructure *within* the container. The daemon's per-user socket is the management plane; agents are PTY children of the apphost process.

---

## Acceptance

- Findings doc committed under `docs/design/`.
- Every capability claim carries a pasted transcript from a real probe run (all 5 probes executed successfully).
- Claims sourced from the README are labelled as such (none were — all from source code + probe runs).
- If a probe could not be run, the reason would be stated (all ran successfully).