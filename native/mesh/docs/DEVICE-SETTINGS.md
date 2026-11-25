<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Radio settings: what the app's screens expect from the engine
Status: implemented for reading (both networks) and for writing (Meshtastic only), see `API.md`. The reply is `{radio, state, note, values}` and `values` has the shape below, plus `firmware`, `hardware` (the board model the radio reports) and `writable` (the paths `device.apply` accepts for this radio; empty for MeshCore). The app's screens show "not read from the radio yet" for every value the engine does not give. Field names
follow the settings the official clients show; the exact option lists (regions, presets, roles) are taken from each
project's own sources by the engine and handed over in `options`, so the app never hard-codes them.
## `device.config {radio}` (owner only)
Meshtastic:
```
{"network":"meshtastic",
 "owner":{"long_name":"...","short_name":"..."},
 "lora":{"region":"EU_868","modem_preset":"LONG_FAST"},
 "device":{"role":"CLIENT"},
 "position":{"broadcast_secs":900},   // precision_bits is per channel and is not read yet
 "channels":[{"index":0,"name":"...","role":"PRIMARY","psk_b64":"...","share_url":"https://..."}],
 "options":{"region":[...],"modem_preset":[...],"role":[...]}}
```
MeshCore:
```
{"network":"meshcore","name":"...",
 "radio":{"region_preset":"...","freq_mhz":869.618,"bw_khz":62.5,"sf":8,"cr":8},
 "advert":{"interval_min":240,"share_location":true},
 "contacts":[{"name":"...","type":"contact|room|repeater","hops":1}],
 "options":{"region_preset":[...]}}
```
A key that is absent was not read. A value is never defaulted. `share_url` is the project's own channel-share link; the app
draws it as a QR code and does nothing else with it.
## `device.apply {radio, changes}` (owner only)
`changes` maps a dotted path (`owner.long_name`, `channels.1.psk` with the value `"rotate"`) to its new value. The engine
answers per path. It must refuse unless the radio is connected, and the app only calls it from a confirm step after
`health.device_config_write` is true. Writing is never exercised by the app's tests.
