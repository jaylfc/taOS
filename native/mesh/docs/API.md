# taos-mesh: engine, local API and CLI

`taos-mesh` is the UI-less mesh radio engine. It talks to Meshtastic and MeshCore radios over USB serial and TCP,
stores every frame, attributes frames to radio identities, enforces owner consent for agents, and serves a local
JSON API. Faces (the Survival Studio app, the `taos-mesh` CLI, agent tools) all use that API.

Status and gaps are in "Not done" at the end. Read it before relying on anything.

## Running

```
taos-mesh daemon [--data-dir D] [--socket S] [--owner IDENTITY] [--device PATH|tcp:HOST:PORT]... [--network N] [--receive-only]
```

* `--owner` is the taOS identity the store belongs to (default `person:$USER`; the app sets it to the signed-in person's account), stamped on every row.
* `--device` connects a radio at start (a serial path or `tcp:host:port`); without `--network` the engine runs the
  read-only handshake to find out which network it speaks. Nothing is connected unless named: there is no
  auto-connect.
* `--receive-only` makes the daemon refuse to write any send to a radio (sends are stored and marked failed with the
  reason). Use it when no transmission must happen.
* Data directory default: `$XDG_DATA_HOME/taos-mesh` or `~/.local/share/taos-mesh`. Socket default:
  `$XDG_RUNTIME_DIR/taos-mesh.sock`, mode 0600.

The handshake writes only host-link requests (Meshtastic `want_config_id` after the 32 wake bytes the reference
client sends; MeshCore `APP_START`, `DEVICE_QUERY`, `GET_CONTACTS`, `SYNC_NEXT_MESSAGE`). It never sets the radio
clock or any setting. Radio transmission happens only through `send`.

## Protocol

Newline-delimited JSON over the unix socket.

```
-> {"id": 1, "method": "messages", "principal": "owner", "params": {...}}
<- {"id": 1, "ok": true,  "result": ...}
<- {"id": 1, "ok": false, "error": {"code": "no_grant", "message": "..."}}
```

### Who may connect, and as whom

1. The socket is created inside a fresh 0700 directory and moved into place with mode 0600, so it is never
   reachable with a wider mode. It lives in `$XDG_RUNTIME_DIR` (the daemon refuses to start without it unless
   `--socket` names a path). Its directory must belong to the owner and not be writable by everyone. The daemon
   will not replace a file that is not a socket, or a socket that belongs to another user.
2. Every connection's peer uid is read with `SO_PEERCRED` and must equal the daemon's owner uid; anything else is
   refused before a request is read.
3. `principal` is `owner` (default), `person:<account id>` or `agent:<taOS agent id>`. `owner` means a connection
   that passed step 2. Any other principal is accepted only with a capability token (`"token"` in the request)
   minted by the taOS controller, which authenticates the agent or person itself: agents never talk to this socket
   directly. Until a controller key exists (`<data dir>/controller.key`, at least 32 random bytes, mode 0600,
   made with `taos-mesh controller-key`), the daemon refuses every non-owner principal.

Token format, version 1:

```
token   = hex(payload) "." hex(mac)
payload = JSON {"v":1,"sub":"agent:scout","exp":<ms since 1970>,"methods":["messages","send"]}
mac     = HMAC-SHA256(key, hex(payload))     (the ASCII of the hex string; key = controller.key)
```

The request's `principal` must equal `sub`, the token must not be expired, and the method must be listed (`"*"`
allows all methods the principal could use anyway). Owner-only methods stay owner-only whatever the token says.
`taos-mesh token --sub P [--ttl-secs N] [--methods a,b]` mints one with the same code, for tests and operators.

What each principal sees (enforced inside the engine, below the token check):

* Messages, `app.messages`, `app.identities`, `last_positions`: an agent sees broadcast records on channels it holds
  a `read` grant for, direct messages only with an all-channel `read` grant, and never positions (the position
  records, or the position riding on any message) without a `position` grant. Positions are their own grant.
* `radios` and `app.radios`: owner, or an agent with some read or position grant. Everyone else gets an empty list.
* `health` returns counts and the error text, never a path.

Error codes: `bad_request`, `unknown_method`, `invalid`, `forbidden` (owner-only method), `no_grant` (agent not
allowed or over budget), `no_radio`, `store_error`, `connect_failed`.

### Methods

| Method | Who | Params | Result |
|---|---|---|---|
| `health` | any | none | `{ok, store_error, pending_unwritten, stored_this_run, torn_tail_preserved (a file name), store, receive_only, device_config, device_config_write}`; the last three tell the app whether sending is possible and whether radio settings can be read (`device_config`, true) and written (`device_config_write`, true unless receive-only) |
| `radios` | any | none | list of `{id, network, link, link_kind, ready, connected, identity, frames_in, frames_out, console_bytes, decode_errors, last_error, queued}` |
| `discover` | owner | none | `{serial: [{path, vid, pid, product, manufacturer, hint}], ble: {state: "devices"|"no_adapter", ...}}` |
| `connect` | owner | `device`, optional `network` | `{radio, network}` |
| `messages` | any (see below) | `filter`, optional `as` (owner only) | list of records |
| `last_positions` | any | optional `as` | list of `{identity, name, node, network, position, heard_ms, record}`, newest per identity |
| `send` | owner or granted agent | `network`, `text`, optional `to`, `channel`, `radio` | the stored outbound record (state `queued`) |
| `subscribe` | owner | none | streams `{"event":"record","record":{...}}` lines for every record stored |
| `identities.list` | owner | none | `{contacts, assignments}` |
| `identities.assign` | owner | `node`, `identity` (null to unassign), optional `name` | ok |
| `contacts.create` / `contacts.update` | owner | `name`, `notes` / `contact`, `name`, `notes` | `{contact}` |
| `contacts.told` | owner | `contact`, `told`, `date` | ok |
| `link` | owner | `contact`, `account` (`person:<id>`) | ok |
| `locations.hide` | the linked account | `contact` | ok |
| `grants.list` / `grants.add` / `grants.revoke` | owner | see Grants (kinds `send`, `read`, `position`) | grants |
| `radios.release` | owner | `device` (a serial path) | `{released, was_open, entries_removed}`: closes the radio on that port, stops reconnecting it, forgets it (so the firmware flasher can have the port) |
| `device.config` | owner | `radio` | `{radio, state: loading\|ok, note, values}`; `values` as in DEVICE-SETTINGS.md, read from the radio's own handshake frames |
| `device.apply` | owner | `radio`, `changes` (path to value) | `{radio, applied, note}`; refused in receive-only mode, when the radio is not connected, or for any path not in `values.writable`; nothing is written when any change is refused |
| `app.radios`, `app.identities`, `app.messages`, `app.send` | as above | see Survival Studio view | see below |

`messages` filter (all optional): `identity` (sender or recipient), `node`, `network`, `channel`, `kind`
(`text|position|nodeinfo|telemetry|ack|control|opaque`), `dir` (`in|out`; `state` for send progress, owner only),
`since_ms`, `until_ms`, `limit`, `newest_first`.

### Records

One record per frame, in and out. Fields: `id` (stable), `owner`, `dir` (`in`, `out`, `state`), `network`
(`meshtastic`, `meshcore`), `radio` (the radio's node id), `observer` (identity the radio is assigned to), `kind`,
`channel`, `host_ts_ms`, `radio_ts` (seconds, as the radio reports it), `from`, `to`, `broadcast`, `packet_id`,
`text`, `position` `{lat, lon, alt_m, time, accuracy_m, hdop, pdop, gps_accuracy_mm, precision_bits, sats, source}`,
`signal` `{rssi, snr, hops, hop_limit, hop_start}`, `detail` (node info, telemetry, ack), `raw_hex` (the exact frame
bytes), `sender` and `recipient` (`{identity, name, linked_account}` as known when the frame was stored),
`principal` (outbound: who asked), `trust`, `delivery` (outbound, folded, query results only).

* Unknown is absent, never zero: no hops figure, no position time, no accuracy are simply missing. Meshtastic does
  not state a position accuracy in metres (it sends DOP and a hardware constant), so `accuracy_m` stays empty there
  and the raw figures are kept.
* A text record carries the sender's last known position with `position.source = "last_known"` and the position's
  own `time`. A position frame carries `source = "packet"`.
* `trust`: `signed` or `encrypted` only when the frame itself says so (Meshtastic `xeddsa_signed`, `pki_encrypted`;
  MeshCore signed text type), `claimed` for an unsigned broadcast. Otherwise absent.
* `delivery`: `queued`, `sent` (handed to the radio link), `heard` (an implicit ack from a neighbour), `acknowledged`
  (the recipient's ack; MeshCore: the send-confirmed code), `failed` (with the reason in the state record).

## Identities, contacts and linking

A radio (node id) is assigned to `agent:<id>`, `person:<account id>` or `contact:<id>`. A contact is a named non-taOS
person (name, notes) created by the owner. Frames are stamped with the sender's identity when stored; frames stored
before an assignment are attributed when queried (by the current assignment), and stamped frames keep who they were.

`link` ties a contact to a taOS account. The rule, stated once:

* The observer (the person whose radio heard or sent a frame) keeps everything.
* The linked account receives the contact's radio history: every frame the contact's radios sent (text, positions,
  telemetry, node info), and the direct messages addressed to the contact's radios. So both parties keep both sides
  of their shared conversations.
* Traffic from other people that the observer merely overheard on a channel stays with the observer only.
* `contacts.told` records that the contact has been told positions are stored (a flag and the date the owner
  confirms).
* Once linked, the account may call `locations.hide`: its position history up to that moment stops being returned
  by every API view, including positions riding on messages, and drops out of `last_positions`. The stored lines are
  not touched (see STORAGE.md). Whether deletion should be real is pending the owner's ruling against "kept
  indefinitely".

## Grants (agents)

Agents have no radio access by default. The owner grants, per agent, network and channel:

* `kind: "send"` with optional `max_msgs_per_hour`, `max_bytes_per_hour`, `expires_ms`. A grant with no channel covers
  every channel and direct messages; a channel grant covers broadcast on that channel only.
* `kind: "read"`: the agent may read the owner's records on that channel. A direct message needs an all-channel read
  grant.

A refused send writes nothing to the radio and is itself stored (`kind: "send_refused"`, with the principal and the
reason). The budget is a rolling hour counted from stored outbound records (it survives a restart), and a send that
fails does not use budget. Agents cannot grant, change identities, connect radios or see send progress.

## Survival Studio view

`app.*` methods return exactly the shapes of the app's `MeshBackend` interface (branch `feat/survival-studio`,
`native/apps/survival/engine/meshinterface.h`), so the C++ side maps one to one:

* `app.radios` -> `[{id, name, transport, link (usb|ble|tcp), state (connected|connecting|offline), firmware, hardware, port, batteryPercent}]`.
  `firmware` and `hardware` (board model) are what the radio itself reported (Meshtastic DeviceMetadata, MeshCore
  DEVICE_INFO); empty until it has said. `port` is the serial path the engine opened (what `radios.release` takes).
  `batteryPercent` is -1 until the radio's own device telemetry has been heard.
* `app.identities` -> `[{id, callsign, owner, isSelf, hasPosition, lon, lat, lastHeardMs}]`; `owner` is empty for a
  contact (not a taOS identity).
* `app.messages {thread?}` -> `[{id, threadId, fromId, toId, text, timeMs, outgoing, delivery, trust}]`. A thread is
  a peer node id (direct) or `ch:<network>:<index>` (channel).
* `app.send {thread, text}` -> `{accepted, messageId, error}`.

The C++ side is `SocketMeshBackend` in the Survival Studio app (`native/apps/survival/engine/socketmeshbackend.cpp`). It reads the app's lists from `app.radios` and `app.identities` and builds messages from the full `messages` records (kind `text`) so that each message carries its sender's name, position and signal.
Change notifications: `subscribe` and re-query.

## Radio settings
`device.config {radio}` (owner only: it carries channel keys) returns what the radio reported during its handshake:
Meshtastic `DeviceMetadata` (firmware, board), `Config` (device, position, LoRa), `Channel` frames and the radio's own
`User`; MeshCore `DEVICE_INFO`, `SELF_INFO` and `CHANNEL_INFO` (the engine asks for each channel slot, a read-only
query). The shape is in `DEVICE-SETTINGS.md`. `state` is `loading` until the radio has finished sending; a key that is
absent was not read.

`device.apply {radio, changes}` writes, for **Meshtastic only**, these paths: `owner.long_name`, `owner.short_name`,
`lora.region`, `lora.modem_preset`, `device.role`, `position.broadcast_secs`, `channels.<n>.name`. It sends
`begin_edit_settings`, the sets and `commit_edit_settings` as local admin messages (no session passkey is needed for the
local host link: checked against the firmware's `AdminModule.cpp`), patching the radio's own bytes so unknown fields
survive. The radio may restart to apply a change and is reconnected by the supervisor. MeshCore settings are read only:
its settings commands are not documented in the companion protocol file this engine follows, so none are guessed.
Frames in the tests are encoded by the upstream reference library; no write has been made to a real radio.

`radios.release {device}` closes the radio on a port and stops its reconnect loop, so `taos-firmware` can flash it.

## CLI

```
taos-mesh daemon | radios | discover | health | connect DEVICE [--network N] | release DEVICE | device-config RADIO | device-apply RADIO path=value ... | listen
taos-mesh messages [--identity I] [--channel N] [--network N] [--kind K] [--since MS] [--until MS] [--limit N] [--as WHO]
taos-mesh send --network N --text T [--to NODE] [--channel N] [--radio R] [--as PRINCIPAL]
taos-mesh positions
taos-mesh identities | identities assign NODE IDENTITY [--name N] | unassign NODE | contact NAME [--notes T] | told CONTACT [--date D]
taos-mesh link CONTACT person:ACCOUNT
taos-mesh hide-locations CONTACT --as person:ACCOUNT
taos-mesh grants | grant AGENT --network N [--channel N] [--kind send|read] [--msgs-per-hour N] [--bytes-per-hour N] [--hours H] | revoke ID
```

Output is JSON. Exit status: 0 ok, 1 the engine refused (message on stderr), 2 could not reach it or bad usage.

## Links, reconnect and link state

`connect` takes a serial path, `tcp:host:port` or `ble:AA:BB:CC:DD:EE:FF`. Every radio runs under a supervisor:
when the link drops (unplugged, out of range, reset) it reconnects with exponential back-off, 1 s doubling to 60 s,
and the back-off restarts once a session has stored a frame. Each radio in `radios` carries `state`
(`connected`, `connecting`, `reconnecting`, `offline`), `attempt` (consecutive failed tries), `next_retry_ms` and
`last_error`. The radio keeps one entry (its own node id) across reconnects. The app view reports `reconnecting` as
`connecting`.

BLE: Meshtastic uses ToRadio/FromRadio/FromNum on service `6ba1b218-...`; MeshCore uses the Nordic UART service
`6e400001-...`. Pairing is left to BlueZ: an unpaired radio is paired through BlueZ's agent, and a failure says to
pair it in the system Bluetooth settings.

## Drivers

* Meshtastic: framed protobuf stream (`0x94 0xC3` + big-endian length, over 512 means corruption and the reader
  resynchronises; bytes outside frames are the debug console and are counted). Decodes text, position, node info
  (both the NodeDB entries and node-info packets), telemetry (device, environment, power), routing acks, and keeps
  everything else (waypoints, ATAK, admin, undecryptable packets) as opaque records with their bytes.
  Field numbers come from the `meshtastic/protobufs` files (GPL-3.0, compatible with AGPL).
* MeshCore: companion protocol (`'<'`/`'>'` + little-endian length on serial and TCP; one frame per write or
  notification over BLE). Decodes self info, contacts and adverts with positions, direct and channel messages (v2
  and v3 layouts), send confirmations. Frames it does not know are kept as control records with their bytes.
  The upstream document says it is still in development, so unknown codes are expected.

## Not done (blunt)
* Residual: any process running as the same uid as the daemon passes the peer check and is treated as the owner, so it can read everything, connect radios, send and grant. Agents must therefore never reach this socket: they run in containers (their own uid or namespace, with the runtime directory not mounted). The engine cannot tell an agent from the owner when both are the same uid.

* BLE transport (`ble.rs`): the BlueZ D-Bus code (`BluezGatt`: pair through BlueZ, connect, resolve services,
  read/write/notify) compiles and the layers above it are tested against a fake GATT device, but it has never talked
  to a real BlueZ device. Expect first-contact fixes. The BLE scan is a passive listing of devices BlueZ already
  knows (through `busctl`); it does not start discovery.
* Never exercised on a real radio. Frames in the tests: the Meshtastic `my_info` and `node_info` frames are real
  captures taken from the upstream project's own test files; every other frame (all MeshPackets, all MeshCore
  frames) was built with the upstream reference libraries and decoded by them, not recorded off the air.
  MeshCore layouts follow `MyMesh.cpp` and are untested against real firmware.
* Serial open clears HUPCL before and after opening, as the reference Meshtastic client does (tested on a pty); the
  open itself can still pulse DTR on some USB adapters.
* MeshCore settings writes, firmware management (see taos-firmware), Reticulum, TAK: not part of this engine.
* Settings read and write have only been run against frames from the reference library and a fake link, never a real radio.
* Meshtastic ack with no error from a neighbour is reported as `heard`, from the recipient as `acknowledged`; a
  relay's real implicit-ack semantics were not verified on hardware.
* Queries scan the store file (interim); fine for thousands of records, to be replaced by taOSmd queries.
* No controller exists yet to mint tokens; until it does, only the owner can use the socket. The token scheme is
  defined and tested here, not yet used by a real controller.
* Send size limits (200 bytes Meshtastic, 160 MeshCore) are conservative guesses below the protocols' maxima.
* Airtime accounting counts messages and bytes, not on-air time.
