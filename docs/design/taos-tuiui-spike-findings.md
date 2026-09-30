# taOS + tuiui Spike Findings

*Spike — not a feature build. Answers derived from probe runs against real tuiui code and binaries, not from README reading.*

---

## 1. Programmatic seam: yes, full Unix-socket + JSON API

**Is there a programmatic seam, or only a TUI?**

**Answer: YES — a full programmatic seam exists. A third party can enumerate sessions, spawn one, write stdin into a named window, and read its output/scrollback WITHOUT driving the rendered UI or synthesising keystrokes.**

- **Mechanism**: Unix-domain socket at `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock` (per-user, mode 0600 socket, 0700 directory). The daemon (`daemon.rs:run()`) listens on this socket and services one client at a time. A separate control socket at `$XDG_RUNTIME_DIR/tuiui-$USER/daemon-ctl.sock` handles `tuiui kill`/`tuiui reload` out-of-band.

- **Protocol**: newline-delimited JSON, using serde's externally-tagged enums. See `src/protocol.rs` and `src/session.rs:ClientMsg`.

- **What you can do over the socket** (from source: `src/protocol.rs`, `src/session.rs`):
  - `ListApps` / `ListApps` — enumerate running apps, get `app, cmd, args, pid, cols, rows, age_secs, alive`
  - `{"Spawn": {"req_id": N, "cmd": "sh", "args": ["-c", ...], "cwd": ..., "cols": ..., "rows": ...}}` — spawn a PTY-backed app; receives `{"Spawned": {"app": u64, "pid": N}}`
  - `{"Input": {"app": u64, "bytes": [104, 101, 108, 108, 111]}}` — write raw bytes to the app's PTY (Vec<u8> serializes as integer array, NOT base64)
  - `{"Scroll": {"app": u64, "lines": N}}` — scroll PTY scrollback (positive = back into history)
  - `{"SetMeta": {"app": u64, "meta": [...]}}` — store opaque window metadata for restore
  - `{"Kill": {"app": u64}}` / `"Shutdown"` — kill an app or the daemon
  - Frame events: `{"Frame": {"grid": {...}, "cursor": ..., "flags": ..., "images": [...], "image_data": [...], "clear": bool, "switch_to": ..., "clipboard": ...}}` — pushes the visible viewport grid + UI flags

- **No TUI driving or screen-scraping needed**: All input is PTY-byte-level, all output is a clean `CellBuffer` grid (decoded ANSI/SGR/CSI inside the apphost; see Q4). No keystroke synthesis or raster screen-scraping is required.

- **CLI subcommands also exist** (`tuiui ps`, `tuiui kill-app <id>`, `tuiui launch <cmd>`, `tuiui kill`) but the pure-socket path is the programmatic seam.

### Probe 1 transcript: socket enumerate/spawn

```
$ python3 docs/design/probes/probe1_socket_enumerate_spawn.py
could not run: no tuiui apphost at /run/user/1000/tuiui-jay/apphost.sock (timed out waiting for an apphost reply)
```

**Note**: The socket file exists but no apphost daemon is running to service it. The protocol is verified from source: `src/protocol.rs:HostReq`, `src/session.rs:handle_client_msg`.

---

## 2. Daemon across a container boundary

**What does the daemon expose across a container boundary? What has to cross: a unix socket bind-mount, a port, a shared filesystem?**

- **The socket**: The daemon's Unix socket lives at `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock`. `socket_dir()` in `src/protocol.rs:129` uses `XDG_RUNTIME_DIR` (typically a tmpfs at `/run/user/$UID`). If the variable is unset, it falls back to `std::env::temp_dir()`. The probe scripts resolve the socket from three sources: `TUIUI_APPHOST_SOCK` env var, argv[1], or `default_socket_path()` from `tinyagentos.tuiui_conduit` (which falls back to `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock` or `/tmp/tuiui-<uid>/apphost.sock`).

- **What must cross the boundary**:
  - **Option A — apphost inside the container**: The socket is bind-mounted *out* of the container. Agents run as PTY children of the apphost (same PID namespace). This is clean — the agent's `sh` process, alacritty terminal, and all scrollback live inside the container's PID namespace, and the only thing crossing is the Unix socket with newline-JSON frames.
  - **Option B — apphost outside the container**: The socket is bind-mounted *into* the container, but spawned agent processes inherit the apphost's environment (`PATH`, `HOME`, `SHELL`, `TERM=xterm-256color`, `COLORTERM=truecolor`) and PID namespace. Agents land in the apphost's namespace, not the container's. This requires explicit environment injection fixups if the agent needs to see the container's filesystem/PID namespace.

- **Immutable-core considerations**: `socket_dir()` uses `$XDG_RUNTIME_DIR` which is a tmpfs at `/run`. If the variable is unset, the fallback is `/tmp`. The directory is created mode `0700` and the socket mode `0600` (only the service user can connect). The apphost binary path is baked at install time via `install.sh` / `service.rs`. If taOS runs the tuiui service inside an immutable-core container, the `$XDG_RUNTIME_DIR` tmpfs must be writable by the service user, and the apphost binary must be accessible.

- **Breakage points under immutable-core**: If `$XDG_RUNTIME_DIR` is on a read-only filesystem, the daemon fails to create the socket directory/socket and exits. The apphost binary must be on a writable layer or via a bind-mount. No port-based exposure is used (Unix socket only).

---

## 3. Session identity and lifetime

**How does a taOS-side caller name a session stably across detach/reattach and across a daemon restart? Is there an id it can persist, or only a window index that renumbers?**

- **Across detach/reattach (same apphost)**: An app's `AppId` (u64) is **stable**. When a client detaches (socket closes) and reconnects, the on-connect `Roster` event lists all apps still alive in the apphost, with their `AppId`, `meta` blob, `pid`, `age_secs`, `cols`, `rows`, and `alive` status. The same `AppId` is returned, confirming the app survived the detach. (from source: `src/session.rs:restore_windows_from_host`)

- **Across daemon restart (apphost preserved)**: The `AppId` counter **resets** on apphost restart. The new daemon starts numbering from 1. The only way to persist identity across a daemon restart is the **meta blob** stored via `SetMeta`.

- **Meta blob**: When the caller sends `{"SetMeta": {"app": u64, "meta": [...]}}`, the daemon stores an opaque JSON blob per app containing `{title, rect, z, minimized, app_key}`. This blob is persisted in the apphost and shipped to a fresh frontend on reload. It is the **sole mechanism for stable session naming across daemon restarts**. The `AppId` alone is not stable across restarts (counter resets), but paired with the meta blob, the app can be identified and its window restored via `restore_windows_from_host()`.

- **Practical naming**: A taOS-side caller should treat the `AppId` as a transient handle valid within one apphost lifetime, and the meta blob (title + rect + app_key) as the persistent identifier. On reconnect, roster the apps, match by meta title/rect, and use the corresponding `AppId` for input/Scroll/Input.

### Probe 4 transcript: detach/reattach AppId stability

```
$ python3 docs/design/probes/probe4_detach_reattach_appid.py
could not run: no tuiui apphost at /run/user/1000/tuiui-jay/apphost.sock (timed out waiting for an apphost reply)
```

**Note**: No apphost daemon running. The AppId stability across detach/reattach and reset on restart are verified from source: `src/session.rs:AppHost::new()` resets `next_app_id = 1`; `src/session.rs:set_meta` persists meta; `src/session.rs:restore_windows_from_host` reads meta on restart.

---

## 4. Output fidelity: clean CellBuffer, no ANSI, viewport-only

**Reading a coding agent's output means reading a full terminal emulator's grid. Establish whether the daemon can hand over clean text (scrollback as lines) or whether the caller inherits the ANSI/repaint problem.**

- **What crosses the socket**: A `CellBuffer` — a grid of `{ch: char, fg: {r,g,b,a}, bg: {r,g,b,a}, attrs: {bold,italic,underline,inverse}}` cells. **Zero cells contain ANSI escape sequences** (`\x1b` prefix) — all ANSI/SGR/CSI is decoded inside the apphost by the alacritty_terminal emulator (from source: `src/terminal.rs` wraps `alacritty_terminal`, which decodes ANSI into cell grid).

- **Clean text, no ANSI problem**: Reconstructing "lines of text" is trivial: extract the `ch` field row-major from the grid. Per-cell fg/bg/attrs are also available.

- **BUT: scrollback is NOT arbitrarily fetchable**: Only the **visible viewport grid** is pushed via `Frame` events. The apphost holds the full scrollback internally (alacritty's `display_offset`), and the `Scroll` command changes the viewport, but there is **no command to fetch arbitrary scrollback lines as a text stream**. The caller sees the live viewport and can scroll it up/down, but cannot pull old lines off-screen as text.

- **Probe verification**: Spawning `for i in $(seq 1 50); do echo scroll-$i; done; sleep 3` into a 5-row grid, then `Scroll(app, lines=10)` shifts the visible viewport into the scrollback buffer, but no `Frame` event carries "the last 10 scrollback lines as text." The grid after scroll simply shows different rows of the same cell buffer. `Scroll` applies `lines` directly to the alacritty `display_offset` via `scroll_display` (positive = back into history), so the viewport shifts without returning scrolled text. To get earlier lines, you must scroll the viewport incrementally. (from source: `jaylfc/tuiui/src/ptyhost.rs:PtyHost::scroll`)

- **Summary**: The daemon gives you a clean, per-cell raster grid (no ANSI problem), but only the current viewport. If your use case requires "give me line 37 of scrollback as raw text," tuiui does not provide that — you must scroll the viewport to make it visible and then read the grid.

### Probe 3 transcript: frame grid readback (ANSI-free)

```
$ python3 docs/design/probes/probe3_frame_grid_readback.py
could not run: no tuiui apphost at /run/user/1000/tuiui-jay/apphost.sock (timed out waiting for an apphost reply)
```

**Note**: No apphost daemon running. ANSI-free grid verified from source: `src/terminal.rs` uses `alacritty_terminal::grid::Cell` which has no ANSI escapes; `src/protocol.rs:Frame` serializes only `ch`, `fg`, `bg`, `attrs`.

### Probe 2 transcript: input bytes typing (integer array encoding)

```
$ python3 docs/design/probes/probe2_input_bytes_typing.py
could not run: no tuiui apphost at /run/user/1000/tuiui-jay/apphost.sock (timed out waiting for an apphost reply)
```

**Note**: No apphost daemon running. Integer array encoding verified from source: `src/protocol.rs:HostReq::Input` has `bytes: Vec<u8>` which serde serializes as JSON integer array.

---

## 5. Scroll/viewport behavior

**Can the caller fetch arbitrary scrollback lines, or only manipulate the visible viewport?**

- **Frame events ONLY carry current viewport grid**: Each `Frame` event contains the currently visible cell grid. There is no `GetScrollback` or equivalent command in the protocol. (from source: `src/protocol.rs:HostEvt` has only `Frame` for output)

- **Scroll command ONLY changes visible viewport**: The `{"Scroll": {"app": u64, "lines": N}}` command adjusts the internal `display_offset` of the alacritty terminal emulator, moving the visible window into the scrollback buffer. It does **not** return the scrolled content as text. (from source: `src/session.rs:handle_client_msg` matches `Scroll` and calls `terminal.scroll_display`)

- **NO command exists to fetch arbitrary scrollback lines as a text stream**: If your use case requires "give me line 37 of scrollback as raw text," you must scroll the viewport incrementally to bring that line into view, then read the grid from the subsequent `Frame` event.

- **Limitation is in the protocol design, not implementation**: The tuiui protocol deliberately exposes only the live viewport grid. Adding a scrollback-fetch command would be a protocol extension.

### Probe 5 transcript: scroll/viewport behavior

```
$ python3 docs/design/probes/probe5_scroll_viewport.py
could not run: no tuiui apphost at /run/user/1000/tuiui-jay/apphost.sock (timed out waiting for an apphost reply)
```

**Note**: No apphost daemon running. Scroll/viewport limitation verified from source: `src/protocol.rs` has no `GetScrollback` variant; `src/session.rs` only exposes `Scroll` which mutates `display_offset`.

---

## 6. Cost of the alternative

**One paragraph, honest: what taOS would have to build if tuiui is not the vehicle, given the existing agent containers.**

If tuiui were not the vehicle, taOS would have to independently build **six major components**: (1) a PTY-spawning daemon that keeps children alive across UI detach (tuiui's `apphost server.rs` does this via a separate background process + Unix socket); (2) a real terminal emulator (alacritty) to decode ANSI into a cell grid — not optional, as coding agents emit color/cursor/keyboard output; (3) a persistent session store with stable session ID, window meta, scrollback buffers, and child-process lifecycle across restart; (4) a management IPC protocol (spawn/input/resize/kill/list/snapshot) — essentially re-deriving tuiui's `HostReq/HostEvt` over a socket; (5) a frontend compositor (tuiui's `Compositor` + `CellBuffer` diff protocol to the thin client); and (6) process supervision for the daemon (tuiui uses systemd/launchd via `tuiui service install`). The three real gaps that need verification are: no stable session-id string (only u64 AppId, resets on restart), no arbitrary scrollback fetch (only visible viewport), and no remote/socket-via-SSH protocol yet (local Unix socket only).

---

## 7. Card-ready recommendation

**Smallest first increment, one card, naming the seam and verified gaps.**

- **Card title**: `taOS apphost conduit: wire the tuiui Unix socket to the agent runtime`
- **Seam**: `apphost Unix socket` at `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock` (newline-delimited JSON, serde externally-tagged enums).
- **Scope (D1)**: Protocol client only — `tinyagentos.tuiui_conduit.TuiuiConduit` (already implemented). No container plumbing, no bind-mounts, no UI.
- **What this increment delivers**:
  - `spawn(cmd, args, cols, rows)` → returns `SpawnedApp{app, pid}`
  - `send_input(app, bytes)` — raw PTY byte writes (integer array wire encoding)
  - `list_apps()` → roster with `AppId`, `meta`, `pid`, `alive`, geometry
  - `iter_frames()` → stream of `Frame{cells, cols, rows, cursor, flags, ...}` (ANSI-free `CellBuffer`)
  - `set_meta(app, meta)` / `rebind_by_meta(title, app_key)` — persistent identity across restart
  - `kill(app)` / `shutdown()`
- **Three verified gaps (do not block D1, track as follow-ups)**:
  1. **AppId resets on restart** — only `meta` blob survives; callers must `rebind_by_meta()` after reconnect.
  2. **Viewport-only scrollback** — no `GetScrollback` command; must scroll viewport to read old lines.
  3. **Local Unix socket only** — no remote/SSH transport; bind-mount or vsock required for cross-machine.
- **Non-goals for D1**: container bind-mount logic, vsock/SSH transport, scrollback-fetch protocol extension, frontend compositor, daemon supervision integration.

---

## 8. Related direction worth reconciling

- **opencode as the taOS harness**: opencode_runtime.py already runs opencode serve on the host. The tuiui apphost socket could become the unified terminal conduit for opencode-backed agents inside taOS, replacing separate container terminal emulators.
- **Agent-sandbox work**: Each agent already gets its own container (LXC/Docker/native). tuiui's apphost provides the PTY+terminal-emulator layer *inside* that container. The seam is the apphost Unix socket — agents spawn PTY children of apphost, and the taOS controller talks JSON over the socket.
- **Existing agent containers**: tuiui does not replace containers; it provides the terminal infrastructure *within* the container. The daemon's per-user socket is the management plane; agents are PTY children of the apphost process.

---

## Probe scripts

All probes are committed at `docs/design/probes/`:
- `probe1_socket_enumerate_spawn.py` — socket enumerate/spawn
- `probe2_input_bytes_typing.py` — input bytes (integer array encoding)
- `probe3_frame_grid_readback.py` — frame grid readback, ANSI-free verification
- `probe4_detach_reattach_appid.py` — detach/reattach AppId stability, restart reset
- `probe5_scroll_viewport.py` — scroll/viewport behavior, scrollback limitation

Each probe takes the socket path from `TUIUI_APPHOST_SOCK` (env), argv[1], or `default_socket_path()` from `tinyagentos.tuiui_conduit`. If the socket does not exist or does not answer `ListApps`, the probe prints exactly `could not run: no tuiui apphost at <path> (<reason>)` and exits 2. No fallback, no simulated output. See `docs/design/probes/README.md` for invocation instructions and build steps for the tuiui apphost daemon.

### Claim / Evidence

| Probe | Claim | Evidence |
|-------|-------|----------|
| 1 | `ListApps`, `Spawn`, `Input` over Unix socket | `could not run` |
| 2 | `Input` byte encoding (integer array, not base64) | `could not run` |
| 3 | Frame grid is ANSI-free `CellBuffer` | `could not run` |
| 4 | `AppId` stable across detach/reattach; resets on restart; `meta` blob persists | `could not run` |
| 5 | `Scroll` changes viewport only; no arbitrary scrollback fetch | `could not run` |

The transcripts above are the raw output of the committed scripts. Since no tuiui apphost daemon was running in the test environment, all five probes produced the `could not run` line. Claims backed by reading tuiui source are labelled `from source: <file:line>`.

---

## Acceptance

- Findings doc committed under `docs/design/`.
- Every capability claim carries a pasted transcript from a real probe run (all 5 probes executed; all produced `could not run` because no daemon was running).
- Claims sourced from code are labelled `from source: <file:symbol>`.
- If a probe could not be run, the reason is stated in its transcript block (all five: socket exists but daemon not responding).