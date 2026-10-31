# Local desktop Settings panels: network, Bluetooth, audio, local desktop

**Status:** Proposal, for owner review. No code in this PR.

## Problem

A taOS box can run as a local desktop (a monitor, keyboard and speakers plugged
straight into it, `taos-kiosk.service` showing the taOS shell full screen). On
that box there is no other OS to fall back to, so everything a desktop user
expects to change from a settings screen has to be in taOS Settings: join a
Wi-Fi network, pair a keyboard or headphones, pick an output and set the
volume, and turn the local desktop itself on or off. Today the installer
admits this gap: `install_local_desktop_if_chosen` in
`scripts/install-server.sh` logs that Wi-Fi, Bluetooth and display settings
are not in taOS Settings yet.

This doc maps what exists, fixes an API shape per panel, sketches the UI, and
sets a test plan. Implementation is one PR per panel after review.

## What exists

### Frontend

- `desktop/src/apps/SettingsApp.tsx` is the shell. A panel is added with a
  `Section` union member, a `SECTIONS` entry (id, label, icon), an entry in the
  content map, and, for hardware, an `ADMIN_ONLY` entry. Admin is read from
  `/auth/status` and fails closed. The sidebar is a `nav` with
  `aria-current="page"`; on mobile the sidebar and content swap with a Back
  button. Deep links use the `section` prop.
- `desktop/src/apps/SettingsApp/WifiPanel.tsx` and `BluetoothPanel.tsx` exist
  and are **read only**: they poll `GET /api/hardware/wifi|bluetooth` every
  10s with a stale-response guard and render a list, an "unavailable" card or
  a "turned off" card. Neither has a toggle, connect, pair or password UI.
- Primitives live in `desktop/src/components/ui/` (button, card, input, label,
  switch, tabs). The toggle-row and segmented-choice patterns already used in
  `SettingsApp.tsx` are the ones the new controls reuse.

### Backend

- `tinyagentos/dbus_cli.py` wraps `busctl --json=short` with an argument list
  (no shell), a 10s timeout, and fixed return codes (127 missing, 124
  timeout). It never raises.
- `tinyagentos/routes/hardware_wifi.py`: admin-only `GET /api/hardware/wifi`
  plus `POST .../enabled`, `.../disconnect`, `.../connect`. Connect validates
  SSID (1 to 32 bytes) and PSK (8 to 63 chars or 64 hex) and sends the PSK
  over in-process `dbus-fast`, never on an argv. Documented in
  `docs/hardware-routes.md`.
- `tinyagentos/routes/hardware_bluetooth.py`: admin-only `GET`, `POST .../power`
  and `POST .../device` with a `Literal` action set (connect, disconnect, pair,
  trust, untrust, remove) and regex allow-lists for adapter and device object
  paths. There is no discovery and no pairing agent.
- Audio exists only on the lock screen: `_read_volume` and
  `POST /auth/lock-volume` in `tinyagentos/routes/auth.py` call `wpctl` with
  argument lists and are console-only (loopback plus a header), not admin
  `/api` routes.
- Local desktop: `tinyagentos/desktop_cli.py` (`taos desktop enable|disable`)
  drives `taos-kiosk.service` through `systemctl`, run with sudo. The HTTP
  session-mode switch in `tinyagentos/routes/system.py` only flips between
  kiosk and Plasma Mobile on handsets, under a unit-scoped polkit rule that
  ships in taOSmobile.

### Known gap

The Wi-Fi write routes answer `write_failed` with the hint "polkit rule
50-taos-hardware.rules", but that rule file is not shipped anywhere in this
repo. On a stock install the write routes therefore fail for the unprivileged
`taos` user. Phase 0 below fixes that before any UI work.

## Principles (all panels)

1. **Admin only.** Every route uses `Depends(require_admin)`. The panels stay
   in `ADMIN_ONLY`.
2. **No shell, ever.** Subprocesses use `asyncio.create_subprocess_exec` with
   an argument list. Prefer D-Bus (`busctl` or in-process `dbus-fast`) over
   CLI parsing, since D-Bus gives typed values and no locale-dependent text.
3. **Closed operation set.** Each route accepts a `Literal` action or a fixed
   set of booleans. User strings (SSID, device path, node id) are validated by
   a regex or length check before they reach any argv or D-Bus call. Object
   paths are matched against an allow-list regex.
4. **Secrets off the argv.** PSKs and pairing PINs go over in-process D-Bus,
   never through a process argument list where `ps` could see them.
5. **Privilege by polkit, scoped narrowly.** The `taos` user gets exactly the
   NetworkManager, BlueZ and unit actions the panels need, nothing broader.
   No sudo from the web process.
6. **Honest state.** Read routes return `available: false` (HTTP 200) when the
   service is missing; write routes return 503. After a write, the response
   carries a read-back of the new state, not an echo of the request (the
   pattern `POST /auth/lock-radios` already uses).

## Backend API per panel

### Phase 0: privilege plumbing

Ship `50-taos-hardware.rules` (installed by `scripts/install-server.sh` only
when the box opts into local mode) granting the `taos` user:

- `org.freedesktop.NetworkManager.enable-disable-wifi`,
  `network-control`, `settings.modify.system`;
- `org.bluez` adapter and device methods (BlueZ uses D-Bus policy, so this is
  a `/etc/dbus-1/system.d/` policy file rather than polkit);
- `org.freedesktop.systemd1.manage-units` limited to `taos-kiosk.service`
  (the unit-scoped pattern from the handset session rule).

The installer step and rule text need owner sign-off and a real-device test.

### Network (NetworkManager)

Existing routes stay. Add:

| Route | Body | Notes |
|---|---|---|
| `POST /api/hardware/wifi/scan` | `{iface?}` | `RequestScan` on the device; returns 202, panel re-polls |
| `POST /api/hardware/wifi/forget` | `{ssid}` | deletes saved profiles whose SSID matches exactly |
| `GET /api/hardware/network` | none | wired devices, IPv4/IPv6 addresses, gateway, DNS (read only) |

Wired configuration (static IP, VLAN) is out of scope for the first panel:
getting it wrong strands a headless box. It is listed as an open question.

### Bluetooth (BlueZ)

Existing routes stay. Add:

| Route | Body | Notes |
|---|---|---|
| `POST /api/hardware/bluetooth/discovery` | `{adapter, on: bool}` | `StartDiscovery` / `StopDiscovery`; auto-stops after 60s server side |
| `POST /api/hardware/bluetooth/pair` | `{device}` | registers a taOS pairing agent for this one request |
| `POST /api/hardware/bluetooth/pair/confirm` | `{device, accept: bool, pin?}` | answers a pending `RequestConfirmation` / `RequestPinCode` |

The pairing agent is the hard part: BlueZ calls back into an `Agent1` object
the server exports over `dbus-fast`. It lives for the length of one pairing,
holds the pending prompt in memory, and the panel polls
`GET /api/hardware/bluetooth` which gains a `pending_prompt` field
(`{device, kind: "confirm"|"pin", passkey?}`). A PIN is 4 to 16 digits,
validated before it goes to BlueZ.

### Audio (PipeWire)

New module `tinyagentos/routes/hardware_audio.py`:

| Route | Body | Notes |
|---|---|---|
| `GET /api/hardware/audio` | none | sinks, sources, defaults, volume, mute, `available` |
| `POST /api/hardware/audio/volume` | `{node, volume: 0.0..1.5, muted?}` | `wpctl set-volume` / `set-mute` |
| `POST /api/hardware/audio/default` | `{node}` | `wpctl set-default` |

`node` must match `^[0-9]{1,6}$` and be present in the last read set. PipeWire
runs per user, so commands run with `XDG_RUNTIME_DIR` set to the kiosk user's
runtime dir, as `_read_volume` already does. The lock-screen helpers move
into a small shared `tinyagentos/audio_cli.py` so both callers use one parser;
that move is part of the audio PR, not a drive-by. If `wpctl status` lists no
sinks the response says `no_sink` rather than showing a slider that drives
nothing (the case measured on the handset).

Parsing `wpctl status` text is fragile; the PR should prefer
`pw-dump` (JSON) for reads and keep `wpctl` for writes.

### Local desktop toggle

| Route | Body | Notes |
|---|---|---|
| `GET /api/system/local-desktop` | none | `{available, enabled, active}` from `systemctl is-enabled/is-active taos-kiosk.service` |
| `POST /api/system/local-desktop` | `{enabled: bool}` | `systemctl enable --now` / `disable --now` |

`available` is false when the unit file is not installed (a headless box), and
the panel then explains how to install it (`taos desktop enable`) instead of
showing a switch that cannot work. Turning it off from the local desktop
itself ends the session the user is looking at, so the UI confirms first.

## UI sketches

All panels: `<section aria-label>` root, `h2` title, cards in the existing
style, every control reachable by Tab, `Switch` with `aria-label`, errors in
`role="alert"`, live status in `aria-live="polite"`.

```
Wi-Fi                                    [ On  (o) ]
--------------------------------------------------
Connected:  HomeNet            ▂▄▆█  Disconnect
--------------------------------------------------
Other networks                           [ Scan ]
  CafeGuest      ▂▄▆_   🔒   Join
  Neighbour5G    ▂▄__   🔒   Join
Saved networks                         Forget...
```

Join on a secured network opens an inline password field (`type=password`,
show/hide toggle, Enter submits, Esc cancels). Focus returns to the row
afterwards.

```
Bluetooth                                [ On  (o) ]
--------------------------------------------------
My devices
  MX Keys         Connected        Disconnect  ...
  Headphones      Not connected    Connect     ...
Nearby                       [ Searching... ⏹ ]
  Speaker-01                           Pair
--------------------------------------------------
Pairing dialog (role=dialog, aria-modal):
  "Confirm the code 482 913 shows on Speaker-01"
  [ Cancel ]  [ Pair ]
```

```
Sound
Output   [ HDMI 1 ▾ ]
Volume   🔈 ━━━━━━━●━━━ 🔊   [ Mute ]
Input    [ USB mic ▾ ]      level meter (later)
```

The volume slider is a native `input type=range` with `aria-valuetext`
("62 percent"), arrow keys in 5% steps, and a debounced POST.

```
Local desktop                            [ On  (o) ]
Show the taOS desktop on a screen connected to this box.
```

## Test plan

Unit tests mock the system tools; nothing in CI touches real D-Bus.

- **Backend:** follow `tests/test_hardware_wifi.py`: monkeypatch `busctl`
  (and the `dbus-fast` call sites, and a new `_run_wpctl` / `_systemctl`) with
  fakes that assert the exact argument tuples. Each route gets: happy path,
  service missing (503 / `available: false`), timeout, permission denied, and
  injection attempts (`ssid` with `;`, newlines, NUL; device paths with `..`;
  `node` of `1 && reboot`) which must be rejected before any fake is called.
  Non-admin callers get 403.
- **Pairing agent:** drive the exported `Agent1` methods directly in a test
  with a fake bus, covering confirm, PIN, cancel and the 60s timeout.
- **Frontend:** vitest + RTL per panel, spying on `fetch`, as
  `WifiPanel.test.tsx` does: keyboard-only flows (Tab to Join, Enter, type
  password, Enter), `role="alert"` on failure, StrictMode double-mount, and
  the admin gate in `SettingsApp.test.tsx`.
- **Needs a real device:** polkit and D-Bus policy actually granting the
  actions to `taos`; joining a WPA2 network; pairing a keyboard and an audio
  device; switching HDMI and USB audio; toggling the kiosk unit off and on.
  Each PR lists these.

## Phased plan

1. **Phase 0:** ship the polkit rule and BlueZ D-Bus policy, installed only in
   local mode; `docs/hardware-routes.md` updated. Hardware test required.
2. **Network panel:** add scan, forget and read-only wired info routes; add
   toggle, join, disconnect and forget to `WifiPanel.tsx`.
3. **Bluetooth panel:** discovery and the pairing agent; controls in
   `BluetoothPanel.tsx`.
4. **Audio panel:** `hardware_audio.py`, shared `audio_cli.py`, new
   `AudioPanel.tsx`.
5. **Local desktop panel:** the `taos-kiosk.service` toggle and panel.

Each phase is one PR with its changelog fragment and the
`docs/agent-coordination.md` line the routes gate asks for.

## Open questions

1. Should Phase 0's polkit rule install by default on every Linux box, or only
   when `TAOS_DESKTOP=local` was chosen? This doc assumes local only.
2. Wired network configuration (static IP, DNS): in scope later, or never from
   the web UI because a mistake strands the box?
3. Display settings (resolution, scaling, rotation) under cage: is there an
   API we want to own, or is it a kiosk-setup concern?
4. Should a non-admin local user (a family member at the screen) be allowed
   to change volume? Today the lock screen allows it from the console only.
5. Does the handset (taOSmobile) want these same panels, and if so do its
   polkit rules move into this repo?
