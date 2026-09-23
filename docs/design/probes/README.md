# tuiui Probe Scripts

These probes verify the tuiui apphost Unix socket protocol capabilities.
Each probe connects to a real tuiui apphost daemon — no mocks, no fallbacks.

## Prerequisites

Build and start the tuiui apphost daemon (from jaylfc/tuiui):

```bash
# Clone and build tuiui
git clone https://github.com/jaylfc/tuiui
cd tuiui
cargo build --release

# Start the apphost daemon (creates socket at $XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock)
XDG_RUNTIME_DIR=/run/user/$(id -u) ./target/release/tuiui apphost &
```

The daemon must be running before any probe executes.

## Invocation

Each probe takes the apphost socket path from:
1. `TUIUI_APPHOST_SOCK` environment variable, or
2. First command-line argument, or
3. `default_socket_path()` from `tinyagentos.tuiui_conduit` (falls back to `$XDG_RUNTIME_DIR/tuiui-$USER/apphost.sock` or `/tmp/tuiui-<uid>/apphost.sock`)

### Run all probes:

```bash
# From the repo root
cd docs/design/probes

# Using environment variable (recommended)
export TUIUI_APPHOST_SOCK=/run/user/$(id -u)/tuiui-$(whoami)/apphost.sock
for p in probe{1..5}_*.py; do python3 "$p"; done

# Or pass socket path as argument
for p in probe{1..5}_*.py; do python3 "$p" /run/user/$(id -u)/tuiui-$(whoami)/apphost.sock; done
```

### Run individual probes:

```bash
python3 probe1_socket_enumerate_spawn.py
python3 probe2_input_bytes_typing.py
python3 probe3_frame_grid_readback.py
python3 probe4_detach_reattach_appid.py
python3 probe5_scroll_viewport.py
```

## Exit Codes

- `0` — Probe completed successfully (real apphost run)
- `1` — A check FAILED
- `2` — Could not run: no tuiui apphost at `<path>` (`<reason>`)

The probe prints exactly one `could not run:` line to stdout and exits 2 if:
- Socket file does not exist
- Socket path is not a Unix socket
- Connection fails (permission, refused, timeout)
- `ListApps` request fails or times out

No simulated output is ever produced. A degraded run must fail, not narrate.

## Frame Window Judging

Each probe that checks frame content judges over a timeout window rather than the first frame only. A `first_match` helper scans the frames pushed by the apphost within the conduit timeout; a `TuiuiConduitError` from the timeout ends the window and the probe records `FAILED` only when no matching frame was found. This makes the verdict independent of frame timing.

## Probe Descriptions

| Probe | File | Claim / Evidence |
|-------|------|------------------|
| 1 | `probe1_socket_enumerate_spawn.py` | `ListApps`, `Spawn`, `Input` over Unix socket |
| 2 | `probe2_input_bytes_typing.py` | `Input` byte encoding (integer array, not base64) |
| 3 | `probe3_frame_grid_readback.py` | Frame grid is ANSI-free `CellBuffer` |
| 4 | `probe4_detach_reattach_appid.py` | `AppId` stable across detach/reattach; resets on restart; `meta` blob persists |
| 5 | `probe5_scroll_viewport.py` | `Scroll` changes viewport only; no arbitrary scrollback fetch |

## Example Output (Real Run)

```
$ python3 probe1_socket_enumerate_spawn.py
=== Test 1: ListApps (empty) ===
Result: 0 apps

=== Test 2: Spawn ===
Result: spawned app 1 with pid 12345

=== Test 3: ListApps (with app) ===
Result: 1 apps
  App 1: cmd=sh, pid=12345, alive=True

=== Test 4: Send Input ===
Result: Input echoed in frame
```

## Example Output (No Apphost)

```
$ python3 probe1_socket_enumerate_spawn.py
could not run: no tuiui apphost at /run/user/1000/tuiui-user/apphost.sock (socket does not exist)
$ echo $?
2
```