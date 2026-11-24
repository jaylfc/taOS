# taos-input

The input engine: a phone on the home network becomes the trackpad and keyboard of the taOS desktop
(for example while the desktop is cast to a TV with `taos-cast`). A user service in the desktop session.

* Engine: `taos-input serve` (Rust, no async runtime). Injects with `zwlr_virtual_pointer_v1` and
  `zwp_virtual_keyboard_v1`, reconnects by itself when the compositor restarts.
* Faces: the phone page (`web/`, served by the engine), the CLI below, and the control socket (agent path).

## Security model

* Listens on ONE explicit private address (`--bind auto` picks this machine's home-network address). The wildcard,
  multicast, public addresses and (unless named with `--allow-net`) loopback are refused at start.
* Every connection is checked against the allowed networks (RFC 1918, link-local, IPv6 ULA by default) before a byte is read.
* Pairing: `taos-input pair` (or `{"op":"pair"}` on the control socket) mints a six digit code, valid 5 minutes,
  one use, dead after 5 wrong guesses. Only the person's own session can mint it. The phone sends it to
  `POST /api/pair` and receives a 256 bit device token; the engine stores only its SHA-256 (`devices.json`, 0600).
* The WebSocket handshake must carry the token (as the second `Sec-WebSocket-Protocol` value, never in a URL) and an
  Origin matching the Host. Without a valid token the upgrade is refused with 401 and no frame is ever read.
* One controller at a time; a new one replaces the old. A dropped or revoked connection releases every held button and key.
* Plain HTTP on the LAN: the token can be read by anyone who can sniff the home network. TLS is future work.

## CLI

    taos-input serve [--bind auto|IP] [--port 7981] [--allow-net CIDR]... [--state-dir DIR] [--control PATH]
                     [--theme-file theme.json] [--theme-mode dark|light]
    taos-input status | pair | devices | revoke <id>|--all | stop | ctl '<json>'

## Control socket (agent path)

`$XDG_RUNTIME_DIR/taos-input/control.sock` (0600 in a 0700 directory, or `TAOS_INPUT_CONTROL`). One JSON line in,
one out, every reply has `"ok"`:

    {"op":"status"}  {"op":"pair"}  {"op":"devices"}  {"op":"revoke","id":"..."|"all":true}  {"op":"stop"}
    {"op":"inject","event":{...}}   # the same events a phone sends

## Wire protocol (WebSocket text frames, JSON)

Phone to engine: `touch {p: down|move|up|cancel, i, x, y, ts}`, `ptr {dx,dy}`, `abs {x,y}` (0 to 1), `btn {b: left|right|middle, d}`,
`scroll {dx,dy}`, `text {s}`, `key {k, m:[ctrl|alt|shift|super], a: tap|down|up}` (`k` is a DOM key name or one character),
`mods {m:[...]}` (held set), `ws {d: next|prev}`, `cfg {sens, natural, scroll}`, `ping {n}`.
Engine to phone: `hello`, `haptic {k: tap|drag|switch}`, `pong {n}`, `state {compositor}`.

Gestures (in the engine, `src/gesture.rs`): one finger moves with speed based acceleration, tap is a left click, hold is a
drag, two fingers scroll (natural by default) and a two finger tap is a right click, a three finger horizontal swipe switches
workspace on the focused output, a three finger tap is a middle click.

## Tests

`cargo test -p taos-input`: unit tests (gestures, keymap, auth, network policy, coalescing) and `tests/e2e.rs`, which runs the
real engine against a private headless sway (pixman, own runtime dir) with a Wayland probe window (`examples/probe.rs`) and
`foot`. `TAOS_E2E=1` turns a missing sway or foot into a failure. `tests/browser/page_check.py` drives the page in a
headless Chromium with touch emulation (manual, needs Playwright).

## Known limits

Sway 1.10 drops scroll deltas that precede an `axis_stop`, so the end-of-scroll marker is not sent (no kinetic scroll in
GTK). iOS Safari has no vibration API, so haptics work on Android only. The page is plain HTTP, so browsers do not offer a
service worker or the Android install prompt; "Add to Home Screen" works. Theme tokens are read when the page loads.
