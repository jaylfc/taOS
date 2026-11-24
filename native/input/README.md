# taos-input

The input engine: a phone on the home network becomes the trackpad and keyboard of the taOS desktop
(for example while the desktop is cast to a TV with `taos-cast`). A user service in the desktop session.

* Engine: `taos-input serve` (Rust, no async runtime). Injects with `zwlr_virtual_pointer_v1` and
  `zwp_virtual_keyboard_v1`, reconnects by itself when the compositor restarts.
* Faces: the phone page (`web/`, served by the engine), the CLI below, and the control socket (agent path).

## Security model

Plain HTTP on the home network is assumed hostile to eavesdroppers, so nothing secret and no typed text crosses it in the clear, and
no browser certificate warning is needed.

**Where it listens.** One explicit private LAN address (`--bind auto` picks this machine's; the wildcard, multicast, public
addresses and, unless named with `--allow-net`, loopback are refused at start). Every connection is checked against the allowed
networks (RFC 1918, link-local, IPv6 ULA by default) before a byte is read. Requests must carry the engine's own `Host` (anything
else gets 421: DNS rebinding), a matching `Origin` if they carry one, and pairing posts must be `application/json`.

**Pairing (once per phone).** `taos-input pair` (or `{"op":"pair"}` on the control socket; only the person's own session can) mints a
six digit code, valid 5 minutes, one use. The code is never sent. The phone and the engine run a CPace style password
authenticated key exchange on ristretto255 (`src/pake.rs`; curve25519-dalek on the engine, @noble/curves 1.9.7 vendored in
`web/vendor/` on the phone; the two group maps are pinned to the same known answers): generator = H2G(code, session id), one
point each, key = DH, then key confirmation MACs in both directions. An eavesdropper sees two group elements and two MACs and can
test no guess offline; an active attacker gets exactly one guess per exchange, and every exchange is counted (3 per source address
per code, 12 per code, 20 starts a minute per source), so a noisy device cannot burn the owner's code and the owner can always ask
for a new one. Inside the exchange's keys the phone sends its public key (P-256, generated on the phone; the private part never leaves it and is kept in the page's own IndexedDB) and the engine sends its own public key, which the phone pins. The engine stores only the phone's public
key (`devices.json`, 0600) and its own identity key (`identity.key`, 0600). There is no bearer token anywhere.

**Every connection** (WebSocket subprotocol `taos-input.v2`, binary frames):

    phone  -> engine   0x01 | device id (6) | nonce (16) | ephemeral P-256 public (65)
    engine -> phone    0x02 | nonce (16) | ephemeral public (65) | ECDSA signature by the engine identity (64)
    phone  -> engine   0x03 | ECDSA signature by the device key (64)
    both               channel keys = HKDF-SHA256(ECDH secret, salt = SHA-256(both nonces), "taos-input/v2/keys")

Each signature covers the device id, both nonces and both ephemeral keys under a role label. The phone refuses an engine whose
signature does not verify against the key it pinned; the engine refuses a device whose signature does not verify against the stored
public key. Nothing is read as input before then. After that every message is `0x10 | counter (8, big endian) | AES-256-GCM(JSON)`,
one key per direction, the counter in the nonce and the associated data, and only the next counter is accepted: a replayed,
reordered, dropped, tampered or plaintext frame ends the connection. A dropped or revoked connection releases every held
button and key (also for a control-socket client that disconnects). One controller at a time.

**Threat model.**
* LAN eavesdropper: sees ciphertext only; cannot brute-force the six digits offline; learns no device secret (none is ever sent).
* LAN active attacker: can guess the code at most once per exchange (counted and limited); cannot impersonate the engine (pinned
  signature) or the phone (device signature); cannot replay or reorder frames. Forward secrecy comes from the ephemeral ECDH.
  They can still block traffic (denial of service) and see that a controller exists and its size and timing.
* Malicious web page in the phone's browser: cross-origin requests fail the Origin and JSON content-type checks, a DNS rebound page
  fails the Host check, and another site cannot read this origin's IndexedDB. (The key is raw bytes there, not a non-extractable
  WebCrypto key, because `crypto.subtle` does not exist on plain HTTP pages; all primitives therefore come from the vendored
  noble libraries, `web/vendor/NOTICE`.)
* Not covered: someone who can already run code as the person on the desktop (they can use the control socket anyway), a phone
  that is unlocked in someone else's hands (revoke it: `taos-input revoke`), malware inside the phone's browser profile (it can read the device key), and the first-contact trust in the page itself: the page is served over plain HTTP, so an active
  attacker on the LAN could serve a modified page at pairing time. Pairing a phone on a home network you trust remains the
  assumption; TLS or a signed page would remove it and is future work.

## CLI

    taos-input serve [--bind auto|IP] [--port 7981] [--allow-net CIDR]... [--state-dir DIR] [--control PATH]
                     [--theme-file theme.json] [--theme-mode dark|light]
    taos-input status | pair | devices | revoke <id>|--all | stop | ctl '<json>'

## Control socket (agent path)

`$XDG_RUNTIME_DIR/taos-input/control.sock` (0600 in a 0700 directory, or `TAOS_INPUT_CONTROL`). One JSON line in,
one out, every reply has `"ok"`:

    {"op":"status"}  {"op":"pair"}  {"op":"devices"}  {"op":"revoke","id":"..."|"all":true}  {"op":"stop"}
    {"op":"inject","event":{...}}   # the same events a phone sends

## Messages (the JSON inside the encrypted frames)

Phone to engine: `touch {p: down|move|up|cancel, i, x, y, ts}`, `ptr {dx,dy}`, `abs {x,y}` (0 to 1), `btn {b: left|right|middle, d}`,
`scroll {dx,dy}`, `text {s}`, `key {k, m:[ctrl|alt|shift|super], a: tap|down|up}` (`k` is a DOM key name or one character),
`mods {m:[...]}` (held set), `ws {d: next|prev}`, `cfg {sens, natural, scroll}`, `ping {n}`.
Engine to phone: `hello` (first frame, also proves the channel),  `haptic {k: tap|drag|switch}`, `pong {n}`, `state {compositor}`.

Gestures (in the engine, `src/gesture.rs`): one finger moves with speed based acceleration, tap is a left click, hold is a
drag, two fingers scroll (natural by default) and a two finger tap is a right click, a three finger horizontal swipe switches
workspace on the focused output, a three finger tap is a middle click.

## Tests

`cargo test -p taos-input`: unit tests (gestures, keymap, auth, network policy, coalescing) and `tests/e2e.rs`, which runs the
real engine against a private headless sway (pixman, own runtime dir) with a Wayland probe window (`examples/probe.rs`) and
`foot`. `TAOS_E2E=1` turns a missing sway or foot into a failure. `tests/browser/page_check.py` drives the page in a
headless Chromium with touch emulation (manual, needs Playwright); `tests/js/interop.cjs` runs the page's own `crypto.js` under node against the real engine (part of the e2e test; the engine side of the protocol is `src/client.rs`, the reference client).

## Known limits

Sway 1.10 drops scroll deltas that precede an `axis_stop`, so the end-of-scroll marker is not sent (no kinetic scroll in
GTK). iOS Safari has no vibration API, so haptics work on Android only. The page is plain HTTP, so browsers do not offer a
service worker or the Android install prompt; "Add to Home Screen" works. Theme tokens are read when the page loads.
