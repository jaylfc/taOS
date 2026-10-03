# Orb Studio live preview: options for emulating an ESP32 round-display device in the browser

**Card:** tsk-klg6o7 · **Date:** 2026-10-03 · **Lane:** research · **Deliverable:** docs only

Jay, 2026-10-02: *"the Orb Studio needs a live preview/emulator for the designer etc so that
needs researching"*.

Orb is the ESP32 agent controller: a round display, BLE, and voice. Orb Studio is the
browser designer where people build Orb faces, apps and launcher screens. This note
compares the ways to give Orb Studio a live preview, scores each on the axes that decide
it (licence, offline/self-hostable, fidelity, latency, payload size, effort to embed in a
taOS web app), and recommends a v1 and a v2.

Every factual claim below carries a URL. Anything I measured myself is labelled with the
date and the command, and anything I could not verify is labelled as unverified rather
than guessed.

## 0. Constraints and assumptions

**Licence.** taOS ships AGPL-3.0-or-later only, and
[`docs/dependency-licences.md`](../../docs/dependency-licences.md) records every licence
election as a row. Anything that is proprietary, non-commercial, or SaaS-token-gated is
therefore a hard no for a shipped feature, and is flagged as such below. The FSF's
compatibility list is the yardstick:
[AGPLv3 is GPLv3 plus a section 13 network clause, and is *not* compatible with GPLv2
alone](https://www.gnu.org/licenses/license-list.html#GPLIncompatibleLicenses), while
[Apache-2.0, Expat/MIT, ISC and 3-clause BSD are all GPL-compatible](https://www.gnu.org/licenses/license-list.html#GPLCompatibleLicenses)
and [LGPL-2.1 is compatible with GPLv2 and GPLv3](https://www.gnu.org/licenses/license-list.html#LGPLv2.1).
So: permissive licences are fine, `GPL-2.0-or-later` is fine (we exercise the "or later"
arm), `GPL-2.0-only` is not, and no NC/proprietary/BSL/BUSL code is.

**Assumption, stated because it changes the answer.** This research assumes Orb Studio
emits firmware that (a) has a portable UI layer separable from the chip drivers, and
(b) is flashable as a complete image. If Orb is built on LVGL, option B is a small
project. If it is built on a LovyanGFX/Arduino_GFX-style draw API, option B still works
because those libraries draw into a plain framebuffer
([`airpocket-soundman/web-simulator-for-lvgl` ships exactly such an M5GFX-compatible
framebuffer backend](https://github.com/airpocket-soundman/web-simulator-for-lvgl)).
Either way the v2 answer (QEMU) only needs (b).

**Where the preview would live.** taOS's desktop app is Vite + React
([`desktop/package.json`](../../desktop/package.json)) and the server already has a
WebSocket surface ([`tinyagentos/routes/event_stream.py`](../../tinyagentos/routes/event_stream.py),
[`tinyagentos/routes/streaming.py`](../../tinyagentos/routes/streaming.py)) plus a
central header policy ([`tinyagentos/middleware/security_headers.py`](../../tinyagentos/middleware/security_headers.py)).
Those three facts set the embed cost for every option below.

## 1. Scorecard

Fidelity is scored against what Orb actually has: round display, touch/rotary input, BLE,
voice/audio.

| Option | Licence, AGPL-3.0 ok? | Offline / self-host | Display | Touch | BLE | Audio | Latency | Payload | Embed effort |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **A1. Wokwi** | CLI is MIT, **the service is proprietary + non-commercial + token-gated: no** | no (cloud; on-prem only by sales) | good | good | good | partial | cloud RTT | 0 | n/a |
| **A2. QEMU-for-ESP32, native on the host** | GPL-2.0-or-later, **yes** | yes | exact, virtual RGB panel | yes | **no** | **no** | frame time | 0 to client | medium |
| **A3. QEMU-for-ESP32 compiled to wasm** | GPL-2.0-or-later, **yes** | yes | unproven, see 2.3 | unproven | **no** | **no** | interpreter-grade | **33 MB** measured on a sibling project | **high** |
| **A4. A small bare-metal emulator in wasm** | MIT / Apache-2.0, **yes** | yes | depends what you write | yes | no | no | interpreter-grade | **221 KB** measured, **unrelated ISA** | very high (you write the SoC) |
| **B1. LVGL (or GFX) compiled to wasm, in the page** | MIT + MIT (Emscripten), **yes** | yes, fully static | real LVGL draw units, pixel exact | yes, mouse/touch mapped by SDL | no (stub the API) | stub, or Web Audio | one frame budget | unmeasured, expect single-digit MB | **low** |
| **B2. Same, via a ready-made browser simulator** | MIT, **yes** | yes, `file://` | real LVGL, or M5GFX-compatible framebuffer | yes | no | no | one frame budget | unmeasured | **low** |
| **B3. SquareLine Studio / LVGL UI Editor preview** | proprietary + paid, and their licences forbid embedding: **no** | desktop app | good | good | no | no | n/a | n/a | n/a |
| **B4. ESP-IDF host target (`idf.py --preview set-target linux`)** | Apache-2.0, **yes** | yes | driver-dependent, SPI is mock-only | partial | no | no | native | 0 | medium-high |
| **C1. Host-side QEMU-Xtensa, frames over WebSocket** | **AGPL-3.0 (Velxio) or GPL-2.0-or-later (Espressif QEMU), yes** | yes, Docker or native | exact firmware pixels, via QEMU's virtual RGB panel | yes, back over the socket | shim only, see the BLE note | no (I2S not emulated) | frame time + LAN RTT | 33 to 43 MB on the *server*, ~0 to the client | **medium** |
| **C2. Host LVGL/SDL window, frames over WebSocket** | MIT + zlib, **yes** | yes | real LVGL | yes | stub | yes, via Web Audio | frame time + LAN RTT | 0 to client | medium |
| **C3. Real Orb over Web Serial / Web Bluetooth** | platform APIs, **yes** | offline once loaded | **real device** | **real device** | **real device** | **real device** | USB / BLE RTT | 0 | low, but needs hardware |

Three sections follow this table in the same A, B, C order: A is full ESP32 emulation,
B is the UI layer running natively in the browser, C is a host-side simulator streamed to the
page. The "unscored prior art" subsection in 3.4 is deliberately absent from the table because
it parses draw calls instead of executing anything.

Two facts drive everything after this table:

1. **Nothing emulates BLE or audio.** Espressif's own QEMU support matrix marks
   **Bluetooth, Wi-Fi, USB, GP SPI, I2C, I2S, RMT, ULP and the GPIO matrix as unsupported
   on every ESP32 target** ([support
   matrix](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md)). Voice
   and BLE therefore have to be previewed against real hardware (C3), not against any
   emulator. Plan for that rather than discovering it in v2.
   The same page also says *"At the moment, Espressif does not provide support for QEMU"*
   (same URL), and that sentence deserves a precise reading, because it looks like it
   contradicts section 2.2 and the recommendation in section 6. It does not.
   **"Support" there means a support commitment, not fitness**: the paragraph continues that
   they *"appreciate issue reports
   but keep in mind that our response may be delayed"* and *"will also likely not be able to
   help with particular use cases which aren't supported yet (e.g. due to missing emulation
   of some peripherals)"*. So `idf.py qemu` is a real, documented, shipped ESP-IDF command
   ([ESP-IDF QEMU guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/qemu.html))
   whose peripheral gaps are published; what you do not get is anyone answering a bug report.
   That is a maintenance-risk finding, and it is why section 6 ranks **Velxio (C1) above
   Espressif's fork (A2)** for v2 rather than the other way round: same fidelity class,
   but one has a vendor behind it and the other is best-effort. It does not make A2
   unusable, and it is not a licence or capability finding at all.
2. **The display is not the hard part.** Espressif's QEMU fork implements a *virtual RGB
   panel with a virtual framebuffer*, explicitly *"not a real hardware peripheral, it is
   used to simplify GUI testing"*
   ([support matrix](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md),
   [ESP-IDF QEMU guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/qemu.html)),
   and ESP-IDF ships [`espressif/esp_lcd_qemu_rgb`](https://components.espressif.com/components/espressif/esp_lcd_qemu_rgb),
   an `esp_lcd`-compatible driver for it. So the Orb UI only has to be written against
   `esp_lcd`, and one build flag swaps the round panel for the virtual framebuffer.

## 2. Option A: full ESP32 emulation

### 2.1 A1. Wokwi: excellent, and unusable

`wokwi-cli` is [MIT licensed](https://github.com/wokwi/wokwi-cli)
([`LICENSE`](https://raw.githubusercontent.com/wokwi/wokwi-cli/main/LICENSE)) and Espressif documents
it as the supported way to run and screenshot ESP-IDF projects in CI, including
`--screenshot-part` / `--screenshot-time` / `--screenshot-file`
([ESP-IDF Wokwi page](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/third-party-tools/wokwi.html)).
It has a GDB server, MCP mode, and diagram linting. Everything about it is unusable for us:

- It needs a token: `WOKWI_CLI_TOKEN`, and *"a valid Wokwi CLI token starts with `wok_`
  and is exactly 44 characters"*
  ([CLI usage](https://docs.wokwi.com/wokwi-ci/cli-usage)).
- *"The simulation runs in the cloud"*; *"The server receives your firmware binary,
  simulates it, and streams the serial output back"*; and *"If you do not want to upload
  your firmware to the cloud, please contact us to discuss options for on-premise
  deployment"* ([Wokwi CI getting started](https://docs.wokwi.com/wokwi-ci/getting-started)).
  The only self-hosted reference anywhere is a warning that the CLI *"prints a warning and
  runs the simulation without the debugger"* against a self-hosted server
  ([CLI usage](https://docs.wokwi.com/wokwi-ci/cli-usage)), so the server is a paid artefact;
  the private gateway and CI minutes are paid tiers
  ([pricing](https://wokwi.com/pricing)).
- The terms grant use *"for your personal and non-commercial purposes only"* and forbid
  copying, *"execute publicly"*, adapting and derivative works of the service's software
  without written authorisation ([Wokwi ToS](https://wokwi.com/legal/terms)). The operator is
  Wokwi B.V. ([copyright policy](https://wokwi.com/legal/copyright)).

Verdict: reference implementation and screenshot-regression oracle for our own CI, at most.
Never a dependency, and never a preview surface for a user.

### 2.2 A2. Espressif's QEMU, natively on the taOS host

Highest fidelity to what Orb actually ships, and the reason it is **not** the first v2
recommendation is stated up front rather than buried: Espressif publishes the peripheral
gaps but **offers no support** for this fork (see fact 1 in section 1 — *"At the moment,
Espressif does not provide support for QEMU"*, with a delayed-response caveat and an
explicit refusal to help with *"particular use cases which aren't supported yet"*). So treat
this as a dependency you maintain yourself, not one someone answers bug reports for. Section
6 puts Velxio (C1) first for exactly that reason.

- `idf.py qemu monitor` builds and boots the app;
  `idf.py qemu --graphics monitor` opens the virtual framebuffer window
  ([ESP-IDF QEMU guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/qemu.html)).
- Configure with `--target-list=xtensa-softmmu --enable-sdl --enable-gcrypt --enable-slirp`;
  prebuilt binaries ship as `qemu-xtensa` and `qemu-riscv32`
  ([ESP-IDF QEMU prerequisites](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/qemu.html),
  [qemu/README.md](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md)).
- The display is `-display sdl` or `-display gtk`, and the guest binds
  [`esp_lcd_qemu_rgb`](https://components.espressif.com/components/espressif/esp_lcd_qemu_rgb)
  ([qemu/esp32/README.md](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/esp32/README.md)).
- Licence: `hw/xtensa/esp32.c` is *"GNU General Public License version 2 **or (at your
  option) any later version**"* ([source header](https://raw.githubusercontent.com/espressif/qemu/esp-develop/hw/xtensa/esp32.c)),
  and the repo describes itself as *"QEMU as a whole ... version 2"*
  ([repo](https://github.com/espressif/qemu/)). GPL-2.0-**or-later** is usable under
  AGPL-3.0 because we take the later arm; see the
  [FSF note](https://www.gnu.org/licenses/license-list.html#GNUGPLv3). Still a copyleft
  dependency, so it belongs in [`docs/dependency-licences.md`](../../docs/dependency-licences.md).
- Real limits to design around: flash must be 2/4/8/16 MB, PSRAM MMU is not emulated so
  `himem` bank switching does not work, RTC watchdog is not modelled, and *"[t]he SDIO part
  is not implemented"* ([qemu/esp32/README.md](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/esp32/README.md)).
- Maintenance cost, twice over. The fork branch *"will often be rebased on top of the upstream
  master branch and force-pushed"* (same README), so pinning a sha and re-basing on a
  schedule is our job — and because there is no support commitment, that job has no
  fallback when it goes wrong.

### 2.3 A3. QEMU compiled to WebAssembly

This works, and it has been upstreamed, and it is still not where I would start.

- The patch series is
  ["[PATCH 00/10] Enable QEMU to run on browsers"](https://lists.libreplanet.org/archive/html/qemu-devel/2025-04/msg01114.html)
  (Kohei Tokunaga, 2025-04-07): a new TCG backend that emits Wasm and runs translation
  blocks through `WebAssembly.Module` / `WebAssembly.instantiate`, a forked TCI so
  blocks start on the interpreter and only hot blocks get compiled, an Emscripten fiber
  coroutine backend, and a 9pfs workaround. 45 files, 6,561 insertions.
- Build recipe: `emconfigure configure --static --disable-tools --target-list=x86_64-softmmu`
  then `emmake make`, against an `emsdk-wasm32-cross` container with GLib 2.84.0, zlib
  1.3.1, libffi 3.4.7 and Pixman 0.44.2 cross-compiled (same patch, and
  [`ktock/qemu-wasm-sample`](https://github.com/ktock/qemu-wasm-sample)).
- Upstream status, per the maintainer: TCI for 32-bit guests is in QEMU 10.1; 64-bit TCI and
  the Wasm TCG backend are *"Under discussion"*
  ([ktock/qemu-wasm](https://github.com/ktock/qemu-wasm)). Live demo:
  [ktock.github.io/qemu-wasm-demo](https://ktock.github.io/qemu-wasm-demo/), talk at
  [FOSDEM 2025](https://fosdem.org/2025/schedule/event/fosdem-2025-6290-running-qemu-inside-browser/).
- The closest precedent to Orb is
  [`ericmigi/pebble-qemu-wasm`](https://github.com/ericmigi/pebble-qemu-wasm): *"Pebble
  smartwatch emulator running in the browser. QEMU compiled to WebAssembly boots real
  Pebble firmware and renders the display to an HTML canvas."* Same shape as Orb: a
  round-ish wearable, real firmware, real pixels in a canvas. Its costs are the numbers to
  budget from: **~8,500 lines of C across 27 files** of device model ported to QEMU 10.1,
  `qemu-system-arm.wasm` at **33 MB**, ~17 MB of firmware, and a serving layer that
  *"adds `Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy` headers required
  for `SharedArrayBuffer`"*.
- Cirkit Designer got the same result a different way: an instruction-accurate ESP32-S3
  emulator written from scratch in Rust, compiled to wasm, booting through Espressif's ROM,
  validated against QEMU golden tests, capping the emulated CPU at **8 MHz by default** to
  hold responsiveness, built by *"a small team over about 8 months"*, and shipped only
  inside their commercial product ([their blog](https://www.cirkitdesigner.com/blog/2026-05-05-esp32-s3-simulator),
  [ESP32-S3 simulator page](https://www.cirkitdesigner.com/esp32-simulator)). Proprietary,
  so unusable, but the honest effort and speed anchors: this class of work is person-months,
  and emulated Xtensa is roughly 30x slower than the nominal 240 MHz.

Three integration costs that are specific to taOS, all real:

1. `SharedArrayBuffer` is gated behind COOP/COEP, and *"Pthreads code will not work in
   deployed environment unless these headers are correctly set"*
   ([Emscripten pthreads docs](https://emscripten.org/docs/porting/pthreads.html)). taOS sets
   CSP centrally in
   [`tinyagentos/middleware/security_headers.py`](../../tinyagentos/middleware/security_headers.py),
   so adding COOP/COEP for one page means auditing every cross-origin resource the SPA
   loads. Emscripten also documents no `fork()` and no POSIX signals in wasm, so any
   firmware that forks will not port (same page).
2. The virtual RGB panel is displayed through SDL or GTK. Getting SDL onto a canvas under
   Emscripten is well trodden, but doing it inside Espressif's force-pushed fork is not.
3. Nothing in the wasm QEMU work touches Xtensa. We would be the first to combine
   espressif/qemu with the Wasm backend.

### 2.4 A4. A small bare system emulator in wasm

Worth knowing because it shows the *pattern* is cheap when the emulator is small.
[`lupyuen/nuttx-tinyemu`](https://github.com/lupyuen/nuttx-tinyemu) (**Apache-2.0**) boots
Apache NuttX on a RISC-V machine *in the browser*: *"TinyEMU is a barebones RISC-V Emulator
that runs in a Web Browser. (Thanks to WebAssembly)"*, built with `emcc` via
`make -f Makefile.js`, exporting console, key, mouse and wheel entry points
([README](https://github.com/lupyuen/nuttx-tinyemu),
[the long-form writeup](https://lupyuen.github.io/articles/tinyemu2)).

**Measured 2026-10-03** with
`curl -sL -o /dev/null -w '%{size_download}' https://raw.githubusercontent.com/lupyuen/nuttx-tinyemu/main/docs/riscvemu64-wasm.wasm`:
**221,309 bytes**. Read that number as an **upper bound from an unrelated ISA, not as an
Orb payload estimate**: this is the *64-bit RISC-V* system emulator, a different instruction
set from the Xtensa LX6 we care about, and it ships no display, radio or SoC model at all. Its
only use in this note is the ratio in the scorecard — a small hand-written emulator can be
hundreds of KB, where porting a real peripheral set is tens of MB. Any Orb figure would need
its own measurement (PoC-1 records the wasm size for B1).

The catch: TinyEMU is *"a system emulator for the RISC-V and x86 architectures"*
([bellard.org/tinyemu](https://bellard.org/tinyemu/)), MIT, with SDL framebuffer output and
a JSON config. There is no Xtensa core. So for Orb this is prior art for *"a C system
emulator compiles to wasm and boots firmware in a tab"*, not a component we can use. Writing
an Xtensa LX6 SoC model ourselves is a firmware company, not a designer tool.

## 3. Option B: run the UI layer natively in the browser

### 3.1 B1. LVGL on Emscripten (the official port)

LVGL maintains an Emscripten port, [`lvgl/lv_web_emscripten`](https://github.com/lvgl/lv_web_emscripten),
**MIT licensed** ([LICENSE](https://raw.githubusercontent.com/lvgl/lv_web_emscripten/master/LICENSE))
and LVGL core is **MIT** ([LICENCE.txt](https://raw.githubusercontent.com/lvgl/lvgl/master/LICENCE.txt)).
The build is four commands and produces one `index.html`:

```bash
git clone --recursive https://github.com/lvgl/lv_web_emscripten.git
cd lv_web_emscripten && mkdir cmbuild && cd cmbuild
emcmake cmake .. && emmake make -j4
```

([README](https://github.com/lvgl/lv_web_emscripten)). LVGL's own docs point at it as the
supported way to *"compile UI to HTML (Emscripten)"* and describe it as *"a convenient way
to share developed UIs with stakeholders"* ([LVGL browser docs, v9.6](https://lvgl.io/docs/open/integration/pc/browser)).

The shell contract between the wasm module and the page is small, and the clearest written
description I found is the community
[`LVGL-Web-Emulator`](https://github.com/ZhangKeLiang0627/LVGL-Web-Emulator) README, which
documents a ~90-line C++ shell acting as a virtual board:
`SDL_CreateWindow` on a canvas at the panel's real resolution, `lv_init()`, register a
display whose `flush_cb` blits a full-screen RGB565 buffer, register a pointer indev, call
the app's `ui_init()`, then hand control to the browser with
`emscripten_set_main_loop(main_loop, 0, 1)`. That is the whole trick: **LVGL never knows it
is in a browser.** It is the same source, the same draw units, the same fonts, the same
pixel output as the firmware.

Useful corollaries:

- `-sUSE_SDL=2` makes Emscripten supply SDL2, whose canvas path *is* the browser event
  adapter, so mouse, touch and keyboard all arrive for free. Emscripten ports the real
  SDL2 codebase rather than shimming it ([SDL forum answer on the SDL2
  port](https://discourse.libsdl.org/t/sdl-and-webassembly/24611)).
- Round geometry is not expected to be a special case: it is an arc and/or an image mask,
  both of which are ordinary LVGL draw units, so wasm and silicon should agree. PoC-1 is
  where we prove that rather than assume it.
- Audio is reachable but not free: Web Audio, or SDL2's audio API which Emscripten also
  provides. BlueKitchen picked SDL2 audio precisely so the same BTstack audio code works
  native and in the browser ([their writeup](https://bluekitchen-gmbh.com/bluetooth-unleashed-classic-ble-and-le-audio-in-the-browser/)).

### 3.2 B2. The same recipe without LVGL

Two independent projects show the pattern is not LVGL-specific:

- [`airpocket-soundman/web-simulator-for-lvgl`](https://github.com/airpocket-soundman/web-simulator-for-lvgl),
  **MIT** ([`LICENSE`](https://raw.githubusercontent.com/airpocket-soundman/web-simulator-for-lvgl/main/LICENSE)),
  splits it into a *fixed simulator runtime built once* plus a per-project
  `app.wasm`, keeps the firmware repo down to one small `lvgl-simulator.json`, and the
  output opens straight from `file://` with no server, Docker or nginx. Its M5GFX backend
  renders hardware-independent drawing code into a framebuffer and is explicit that it
  *"does not emulate an M5 board, its SPI bus, or other peripherals"*. Caveat: created
  2026-07-22 with zero stars, so treat it as a working prototype, not a dependency.
- [`tanakamasayuki/LGFXScreenBuilder`](https://github.com/tanakamasayuki/LGFXScreenBuilder),
  **MIT** ([`LICENSE`](https://raw.githubusercontent.com/tanakamasayuki/LGFXScreenBuilder/main/LICENSE)),
  is the clearest statement of the architectural split we want for Orb Studio:
  *"It separates screen design from application logic"* with a browser authoring tool that
  exports a header, plus a *host backend* that renders every profile to PNG for screenshot
  regression tests. Designer in the browser, generated code on the device, one renderer
  shared by the preview and by CI.

### 3.3 B3. The commercial designer previews, and why we cannot use them

Both commercial designers have excellent previews and both are unusable here:

- **LVGL UI Editor** (the successor to LVGL Studio). The LVGL docs already advertise
  *"Preview UIs in the LVGL UI Editor's online preview"* ([browser docs](https://lvgl.io/docs/open/integration/pc/browser)),
  and also state the online share is *"Coming soon"* (same page). Its
  [EULA](https://lvgl.io/pro/legal/eula) defines the Software as including *"any
  browser-based or hosted editor"*, makes the Community and Evaluation licences
  non-commercial, and forbids third parties from using *"the Software, including the CLI
  or any automation interface, as a component of or backend for any third-party UI
  development tool"* without written consent. That clause is aimed squarely at Orb Studio.
  Pricing is per-product, [starting at a paid tier for
  commercial use](https://lvgl.io/pro/pricing).
- **SquareLine Studio** has a pixel-perfect *"Play button ... without needing to rebuild
  it"* ([squareline.io](https://squareline.io/)), but it is a desktop app priced
  [from free-for-personal-non-commercial through 790 USD/year and 220 USD/month for
  business use](https://squareline.io/pricing/licenses), and the preview lives inside that
  app, not behind an embeddable API. Its own site notes LVGL and SquareLine are *"separate,
  independent companies with no official affiliation"*.

### 3.4 Prior art, deliberately unscored: traces rather than execution

Several TFT tools parse `tft.drawRect()` calls instead of running code. Two examples, both
useful as prior art and neither a preview engine:

- [`mdmmt05/Arduino_TFT_simulator`](https://github.com/mdmmt05/Arduino_TFT_simulator) is
  library-agnostic precisely because it *"only parses the command syntax, not library
  implementations"*, and its README lists what it does *not* support: `setFreeFont()`,
  sprites, arcs, `loop()` execution, animation, touch.
- [`59jag59`'s GFX_SIM](https://59jag59.github.io/Simulateur-arduino_gfx/) is the closest
  thing to what we want for a non-LVGL stack: a browser Arduino_GFX simulator for a
  480x320 panel that transpiles Arduino C++ to JS and runs it on a canvas, with touch.
  It is licensed **CC BY-NC 4.0, non-commercial**, so it is a reference, not a dependency.

### 3.5 B4. ESP-IDF's own host target

ESP-IDF can build an app for the host: `idf.py --preview set-target linux`
([ESP-IDF host apps guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/host-apps.html)).
It is Apache-2.0, so licence-clean, and the guide is refreshingly honest: only a *limited*
number of components are ready on Linux, each port is *"limited or different"* than on
chip, and the FreeRTOS POSIX simulator's limitations mean *"these limitations are not very
practical"*. For display work specifically, `spi_flash` is listed mock-only, so an SPI
panel will not work. Same guide: *"simulating the environment and mocking dependencies does
not fully represent the target device"*. Useful for logic tests, wrong tool for a preview.

[`fabiobatsilva/arduinofake`](https://github.com/fabiobatsilva/arduinofake) (**MIT**,
[`LICENSE`](https://raw.githubusercontent.com/fabiobatsilva/arduinofake/master/LICENSE)) is the
opposite trade and worth naming so it is not mistaken for a preview engine: it fakes the
Arduino API so firmware logic can be unit-tested on a host with no board and no display, and
it renders nothing at all. It belongs to the B4 row above — useful for the CI half of a
designer (does this screen's state machine compile and run?), useless for the preview half.
Note its licence lives on the `master` branch; `main/LICENSE` 404s, which is the kind of
detail that matters when we vendor something.

## 4. Option C: a host-side simulator streamed to the page

### 4.1 C1. The finding that changes the v2 plan: Velxio

**Which tree is canonical.** Every Velxio claim in this note comes from one tree:
[`github.com/davidmonterocrespo24/velxio`](https://github.com/davidmonterocrespo24/velxio).
A second repo exists at `github.com/velxio/velxio`, and GitHub labels it **"Public, forked
from davidmonterocrespo24/velxio"** — it is a fork, and it is behind: its `master` HEAD was
2026-09-23 against the canonical tree's 2026-10-02 when checked on 2026-10-03 (both from the
commit atom feeds, e.g.
[canonical commits](https://github.com/davidmonterocrespo24/velxio/commits/master.atom) and
[fork commits](https://github.com/velxio/velxio/commits/master.atom)). Three independent
signs name the canonical one: that tree's own README tells you to
`git clone https://github.com/davidmonterocrespo24/velxio.git`, Espressif's blog links
that same tree when it says the core is open source, and the image is published as
`ghcr.io/davidmonterocrespo24/velxio:master`. An earlier draft of this note cited the fork
for the licence and the canonical tree for the features; that was wrong, and it is fixed
here — the licence verdict and the capability claims now both come from the canonical tree.
The two `LICENSE` files happen to be byte-identical AGPLv3 text (*"Velxio — Copyright (C)
2025 David Montero Crespo"*), which is why the mix-up survived, but a fork is not a licence
source and we should not be relying on that coincidence.

**Licence: AGPL-3.0, dual-licensed.** The LICENSE in that tree is the
[GNU Affero General Public License v3](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/LICENSE),
the same licence taOS already ships. Velxio also offers a paid alternative:
[COMMERCIAL_LICENSE.md](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/COMMERCIAL_LICENSE.md)
states it is *"dual-licensed"*, that AGPLv3 is *"free for everyone, including commercial
use, as long as you comply with the AGPLv3 copyleft terms"*, and that the Commercial License
grants *"the right to use, modify, and integrate Velxio into a proprietary / closed-source
product or service **without** the AGPLv3 source-disclosure obligation"*; its own table
marks *"Open-source projects released under AGPLv3"* as **not** needing the commercial
licence. taOS is AGPL-3.0-or-later
([`docs/dependency-licences.md`](../../docs/dependency-licences.md)), so we take the free
AGPLv3 arm and the verdict stays **Accept**. Three obligations ride along: the vendored
engine must stay AGPLv3; if a preview surface ever has to ship outside AGPL that is a *paid*
commercial licence, not a fork of the service; and the README requires that *"All
contributors must sign a Contributor License Agreement (CLA) so that the dual-licensing
model remains valid"*, which only binds us if we upstream patches.

**What it does.** Espressif's own developer blog describes it as running *"real ESP32
firmware on emulated hardware"* with
[ten ESP32-family boards](https://developer.espressif.com/blog/2026/07/velxio-browser-based-esp32-simulation/),
and states *"The entire emulation core is open source under AGPLv3 at
github.com/davidmonterocrespo24/velxio"* with *"one Docker image"* in which *"every board,
all ESP32 variants included, works offline on your own hardware"* (same blog). That last
quote is also the offline/self-hostable cell in the scorecard; an earlier draft cited a
velxio.dev docs page for it, and I could not reproduce that sentence on the page any more
(it has since been restructured into a per-board page), so the blog is now the single source.

Architecture, from the canonical tree's own docs. The board split is explicit:
*"The browser backends (avr8js, rp2040js) execute in a Web Worker — no roundtrip to the
server during simulation. The QEMU backends run as Python-managed subprocesses and stream
events over WebSocket to the frontend"* (ngspice-wasm is a third path, lazy-loaded for the
electrical layer rather than a board backend)
([docs/emulator.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/emulator.md)).
Xtensa ESP32 and ESP32-S3 run through the lcgamboa QEMU fork *"running as a
`libqemu-xtensa.{dll,so,dylib}` shared library, embedded by the FastAPI backend"*, with the
frontend talking to it over a *"WebSocket bridge (`/ws/sim/{board_id}`)"* (same page), and
the ESP32 path needs Docker or a manual build
([docs/ESP32_EMULATION.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/ESP32_EMULATION.md)),
whose header scopes it to *"ESP32, ESP32-S3 (Xtensa LX6/LX7 architecture)"* — that scoping
is the citation for the LX6/LX7 chip claim, not the blog. The peripheral table lists GPIO,
ADC, UART, I2C, SPI, RMT/NeoPixel, LEDC/PWM, Wi-Fi via SLIRP NAT, and *"BLE | Advertising +
basic GAP via QEMU's BLE shim"*
([emulator.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/emulator.md);
the radio stack has its own page,
[docs/ESP32_WIFI_BLUETOOTH.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/ESP32_WIFI_BLUETOOTH.md)).
The BLE claim is **still unverified by me** and I would not plan around it — see *BLE,
honestly* in section 5.

Two version facts I previously left as a hedge are now settled, and both were stale in the
wrong direction. The engine is [`lcgamboa/qemu`](https://github.com/lcgamboa/qemu), **not**
Espressif's fork, at *"libqemu 1.2.x (lcgamboa QEMU fork, **9.2 base**)"*, and the platform
is *"**arduino-esp32 3.3.10 (IDF 5.5.4)**"* — not the `2.0.17 / IDF 4.4.x` pin this note used
to quote. The same page explains the change and why it matters to us: *"Until August 2026
this document pinned 2.0.17 because the original qemu-8.1.3 WiFi shim crashed on IDF 5.x
cache handling"*, and sketches that still use APIs removed in 3.0 need Espressif's
[2.x to 3.0 migration guide](https://docs.espressif.com/projects/arduino-esp32/en/latest/migration_guides/2.x_to_3.0.html)
(quoted and linked from that page). So the Xtensa path runs **IDF 5.5.x**, which is also
what we build — the risk I flagged as *"verify which is current before designing around it"*
is resolved in our favour.

One scope caveat, because "offline and self-hostable" is a per-board claim and not a blanket
one: the self-hosted image ships *"the Arduino, Raspberry Pi Pico and ESP32 families"*, while
*"STM32, Raspberry Pi Linux, the ESP32-C6 and the partner boards are only available online at
velxio.dev"* ([README](https://github.com/davidmonterocrespo24/velxio)). For Orb that is
exactly the family we need, but the ESP32-C6 is *not* in the self-hosted image, so if Orb
ever moves to a C6 the "self-hostable yes" answer changes.

That is precisely Option C, already AGPL, already self-hostable, already wired for a
browser. The work drops from "build an emulator" to "vendor a service and write a canvas
renderer".

### 4.2 C2. Host LVGL/SDL window, streamed

The lightest option that still runs the *real* UI code. LVGL's SDL driver is first class:
`#define LV_USE_SDL 1` then `lv_sdl_window_create(...)`, `lv_sdl_mouse_create()`,
`lv_sdl_keyboard_create()` ([LVGL SDL driver docs](https://lvgl.io/docs/open/integration/pc/sdl)),
used by the maintained [`lv_port_pc_vscode`](https://github.com/lvgl/lv_port_pc_vscode),
[`lv_port_pc_eclipse`](https://github.com/lvgl/lv_port_pc_eclipse) and
[`lv_port_linux`](https://github.com/lvgl/lv_port_linux) ports (same page). You then stream
the window's framebuffer to the page. [`CastMCU`](https://github.com/Akhil-Chaturvedi/CastMCU)
(**MIT**, [`LICENSE`](https://raw.githubusercontent.com/Akhil-Chaturvedi/CastMCU/main/LICENSE))
is the reference implementation of that loop for real hardware: adapters for
TFT_eSPI, Adafruit_GFX, LovyanGFX, Arduino_GFX *and LVGL* by registering a flush callback,
a command stream over USB serial, a Python viewer, and an *"MJPEG webcam server (--webcam-port 8080,
stream to any browser or ffmpeg)"* plus touch injection back into the device. It also has
`--simulate-spi HZ`, which is a good reminder that the honest thing to model is transfer
time, not just CPU time. Caveat: one star, created 2026-08-09, so read it as a protocol
sketch, not a library.

If you would rather not write a framebuffer pump, VNC works: `wayvnc` is **ISC** and
serves a headless wlroots Wayland session with virtual input devices
([wayvnc](https://github.com/any1/wayvnc)), but it explicitly does **not** support GNOME,
KDE or Weston ([README](https://github.com/any1/wayvnc/blob/master/README.md)), so LVGL's
SDL window under X11 would need `x11vnc` instead. VNC also adds a codec, an auth surface
and latency for no gain over a raw socket, which is why I would not ship it.

### 4.3 C3. Real hardware in the loop

For the two things no emulator covers (BLE, audio) the answer is the real device, and the
browser now has the ports:

- **Web Serial**: Chrome 89+, desktop platforms, HTTPS only, *"Limited availability ...
  not Baseline"* ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Web_Serial_API),
  [Chrome for Developers](https://developer.chrome.com/docs/capabilities/serial)), and
  Chromium-only in practice, since Android browsers and WebView do not expose it at all
  ([WICG/serial](https://github.com/WICG/serial/blob/main/README.md)). This flashes the
  design and drives it over USB CDC.
- **Web Bluetooth**: *"Limited availability"*, experimental, Chromium only, gated by a
  `Permissions-Policy: bluetooth` whose default allowlist is `self` and so blocks
  third-party content by default ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API)).
- A worked example of driving a C stack from the browser over Web Serial is BTstack's
  `web-h4` port ([BTstack build matrix](https://github.com/bluekitchen/btstack/blob/master/README.md)),
  which uses Emscripten Asyncify to suspend C across the async serial API and SDL2 for
  audio ([BlueKitchen writeup](https://bluekitchen-gmbh.com/bluetooth-unleashed-classic-ble-and-le-audio-in-the-browser/)).
  **Read it, do not link it**: BTstack is *"free for non-commercial use. However, for
  commercial use, tell us a bit about your project to get a quote"*
  ([BTstack README](https://github.com/bluekitchen/btstack/blob/master/README.md)), GitHub
  reports its licence as `Other`, and that is a blocker under
  [`docs/dependency-licences.md`](../../docs/dependency-licences.md). The same applies to
  [`thegecko/webbluetooth`](https://github.com/thegecko/webbluetooth/), which is MIT on
  paper while its SimpleBLE dependency has walked MIT → BSD-3 → GPL-3 → **BUSL-1.1**.

## 5. Licence verdicts against AGPL-3.0

| Component | Licence | Verdict |
| --- | --- | --- |
| LVGL + `lv_web_emscripten` | MIT ([core](https://raw.githubusercontent.com/lvgl/lvgl/master/LICENCE.txt), [port](https://raw.githubusercontent.com/lvgl/lv_web_emscripten/master/LICENSE)) | **Accept**, permissive |
| Emscripten | MIT **or** UI/NCSA ([licence page](https://emscripten.org/docs/introducing_emscripten/emscripten_license.html)) | **Accept**, permissive |
| `web-simulator-for-lvgl` | MIT ([LICENSE](https://raw.githubusercontent.com/airpocket-soundman/web-simulator-for-lvgl/main/LICENSE)) | **Accept**, permissive |
| LGFXScreenBuilder | MIT ([LICENSE](https://raw.githubusercontent.com/tanakamasayuki/LGFXScreenBuilder/main/LICENSE)) | **Accept**, permissive |
| CastMCU | MIT ([LICENSE](https://raw.githubusercontent.com/Akhil-Chaturvedi/CastMCU/main/LICENSE)) | **Accept**, permissive; read as a protocol sketch (one star) |
| ArduinoFake | MIT ([LICENSE, `master`](https://raw.githubusercontent.com/fabiobatsilva/arduinofake/master/LICENSE)) | **Accept**, permissive — host unit tests only, renders nothing (see 3.5) |
| `wokwi-cli` (the CLI binary only) | MIT ([LICENSE](https://raw.githubusercontent.com/wokwi/wokwi-cli/main/LICENSE)) | **Accept** in isolation; **blocked in practice** because every use needs the proprietary service below |
| Espressif `qemu` | GPL-2.0-**or-later** ([header](https://raw.githubusercontent.com/espressif/qemu/esp-develop/hw/xtensa/esp32.c)) | **Accept** under AGPL-3.0 via the "or later" arm; record in `docs/dependency-licences.md` |
| Velxio (canonical tree `davidmonterocrespo24/velxio`) | **AGPL-3.0** ([LICENSE](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/LICENSE)), dual-licensed against a paid [Commercial License](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/COMMERCIAL_LICENSE.md) | **Accept** on the free AGPLv3 arm, which we qualify for; must stay AGPL, and any non-AGPL surface is a paid licence |
| Apache NimBLE socket HCI transport | Apache-2.0 ([source](https://github.com/apache/mynewt-nimble/blob/master/nimble/transport/socket/src/ble_hci_socket.c)) | **Accept**, and the licence-clean BLE path |
| TinyEMU / nuttx-tinyemu | MIT ([bellard.org](https://bellard.org/tinyemu/), [repo](https://github.com/lupyuen/nuttx-tinyemu)) | **Accept**, but wrong ISA |
| `wayvnc` | ISC ([repo](https://github.com/any1/wayvnc)) | Accept, but wrong tool for a first-party feature |
| Wokwi service | proprietary, NC, cloud/token gated ([ToS](https://wokwi.com/legal/terms)) | **BLOCKED** |
| SquareLine Studio | proprietary, paid, preview not embeddable ([pricing](https://squareline.io/pricing/licenses)) | **BLOCKED** |
| LVGL UI Editor | proprietary; EULA forbids use as a backend for a third-party UI tool ([EULA](https://lvgl.io/pro/legal/eula)) | **BLOCKED** |
| BTstack | free for non-commercial use, quote for commercial ([README](https://github.com/bluekitchen/btstack/blob/master/README.md)) | **BLOCKED** |
| `thegecko/webbluetooth` | MIT wrapper over BUSL-1.1 SimpleBLE ([README](https://github.com/thegecko/webbluetooth/)) | **BLOCKED** (transitive) |
| GFX_SIM browser Arduino_GFX sim | **CC BY-NC 4.0** ([page](https://59jag59.github.io/Simulateur-arduino_gfx/)) | **BLOCKED** (NC) |
| Cirkit ESP32-S3 simulator | proprietary commercial ([page](https://www.cirkitdesigner.com/esp32-simulator)) | **BLOCKED** |

Two notes for the record. First, `docs/dependency-licences.md` says the repo *"ships under
AGPL-3.0-or-later only ... there is no commercial licence"*, which is exactly why the
**BLOCKED** rows above are non-negotiable rather than "get a quote". Second, if we adopt any
QEMU-derived engine, GPL copyleft and AGPL copyleft combine cleanly, but the build and
distribution of the QEMU binary becomes a documented obligation, not an implementation
detail. That is a discussion for the dependency-licences file, not for this note. Third,
where a project offers a paid commercial licence as an alternative to its copyleft one
(Velxio, and the two commercial designers), that alternative is **not** available to us by
construction: our own licence is the copyleft one, so the free arm is the only arm we can
take, and "there is a commercial licence for sale" is never a reason to relax a verdict.

### BLE, honestly

There is no BLE story in any emulator we can ship:

- Espressif QEMU: Bluetooth unsupported on every target
  ([matrix](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md)).
- Velxio claims *"BLE | Advertising + basic GAP via QEMU's BLE shim"* in the lcgamboa fork
  ([emulator.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/emulator.md);
  the radio stack is documented separately in
  [docs/ESP32_WIFI_BLUETOOTH.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/ESP32_WIFI_BLUETOOTH.md)).
  I have **not** run it, so treat it as a vendor claim rather than a verified capability —
  and note that advertising plus GAP is not a usable Orb radio either way: it does not include
  a GATT server or the notification path our voice-control link needs.
- The licence-clean software route is to keep the firmware's own NimBLE host and replace
  only the transport. Apache NimBLE ships a socket transport that speaks HCI over TCP
  (`BLE_SOCK_TYPE=linux_tcp`, with `socat -x PIPE:/dev/ttyACM1 TCP4-LISTEN:14433` as the
  documented counterpart)
  ([ble_hci_socket.c](https://github.com/apache/mynewt-nimble/blob/master/nimble/transport/socket/src/ble_hci_socket.c),
  [transport design doc](https://github.com/apache/mynewt-nimble/blob/master/nimble/doc/transport.md)),
  and ESP-IDF's host-only examples show the same seam on-device with a UART HCI transport
  ([esp-hosted walkthrough](https://github.com/espressif/esp-hosted-mcu/blob/main/examples/host_nimble_bleprph_host_only_uart_hci/tutorial/bleprph_host_only_walkthrough.md)).
  NimBLE host over a socket to a host-side controller simulator is therefore a real,
  AGPL-clean option. It is also a project of its own, and I have not found anyone who has
  shipped it for an ESP32 UI, so treat it as unexplored.

## 6. Recommendation

### v1: compile the designer output to wasm and run it in the page (Option B)

Pick the designer's **portable UI layer**, compile it with Emscripten against the
`lv_web_emscripten` shell (or the ~90-line equivalent for our stack), and mount it in the
Orb Studio canvas at the real panel resolution.

Why this and not the others for v1:

- It is the only option that is **pixel exact by construction**: the same LVGL draw units,
  fonts and layout code as the firmware, so what the designer sees is what the device
  shows. Option A2 is also exact but needs a full toolchain and boot per change.
- It is **fully offline and self-hosted** with zero server state, which is what taOS wants.
- Licence-clean with room to spare: LVGL and Emscripten are both MIT, no copyleft
  anywhere in the tree.
- Latency is one browser frame budget, because the loop is frame-paced by
  `emscripten_set_main_loop`; no encode, no socket, no codec.
- It is the smallest thing that can be validated: a single screen, a single build command.

Non-goals for v1, stated here so they are not discovered later: no BLE, no audio, no
firmware boot. Those are v2, or real hardware.

### v2: host-side QEMU-Xtensa, streamed to the page (Option C)

Run real Orb firmware on a QEMU Xtensa machine on the taOS host and stream the virtual
framebuffer to the same canvas the v1 preview already uses, with touch going back. Two
sub-choices, and I recommend trying them in this order:

1. **Vendor Velxio's engine** (C1, from the canonical
   `davidmonterocrespo24/velxio` tree). It is AGPL-3.0 like us, self-hostable as one Docker
   image, already does ESP32/ESP32-S3 Xtensa, already bridges to a browser over WebSocket,
   and already has a BLE shim. Its toolchain is **arduino-esp32 3.3.10 on IDF 5.5.4**, which
   matches what we build, so the "wrong IDF version" risk that used to gate this choice is
   gone. We would write the canvas renderer and the Orb board definition, not an emulator.
   This is the cheapest v2 by a wide margin.
   Carve-outs to record before we start: we take the **free AGPLv3 arm** (we qualify — the
   project's own table says AGPLv3 open-source projects do not need the paid commercial
   licence), so the integration stays AGPL forever; we vendor from the canonical tree, not
   the `velxio/velxio` fork; and we pin a sha, because `master` is a moving target that is
   already ahead of the fork by ten days.
2. **Espressif's `idf.py qemu` + `esp_lcd_qemu_rgb`** (A2). Best fidelity to the shipping
   firmware, because it is Espressif's own fork and the tool our build already uses. Costs
   us the QEMU build/pin/re-base chore, a copyleft row in
   [`docs/dependency-licences.md`](../../docs/dependency-licences.md), and **the absence of
   a support commitment**: Espressif publishes the gaps but explicitly does not support the
   fork, so a broken peripheral is our problem to diagnose. That last cost is the main
   reason this is second and not first. Worth it if Velxio's engine turns out to be a
   lcgamboa-fork dead end we cannot patch around.

Explicitly **not** v2: porting QEMU to WebAssembly (A3). The 33 MB payload, the
interpreter-grade speed, the site-wide COOP/COEP requirement and the need to be the first
people to combine Espressif's fork with the Wasm backend are all costs we would pay to look
better on paper.

Either way, **preview BLE and audio on real hardware** (Option C3), behind a "connect your
Orb" button, using Web Serial for flashing and Web Bluetooth for the radio. Accept that
this is Chromium-only and label it as such in the UI.

## 7. Smallest proofs of concept

Deliberately narrow. Each one is a single PR with a screenshot or a green check as its
deliverable.

### PoC-1 (validates v1) -- one Orb screen, live, in the Orb Studio page

- Take the existing round home screen as a standalone C/C++ translation unit plus its
  fonts and images. No firmware, no BLE, no drivers.
- Build it with `emcmake cmake` + `emmake` against a shell in the shape of
  `lv_web_emscripten` (or the documented 6-step shell from
  [`LVGL-Web-Emulator`](https://github.com/ZhangKeLiang0627/LVGL-Web-Emulator)):
  SDL2 canvas at the real resolution, display + `flush_cb`, pointer indev, `ui_init()`,
  `emscripten_set_main_loop`.
- Serve the artifact from taOS (`tinyagentos/` static or the Vite `public/` dir) and mount
  it in the designer page. Single-threaded on purpose: avoid `SharedArrayBuffer`, and
  therefore avoid COOP/COEP entirely, for the first cut.
- **Pass condition:** edit one colour or one label in the designer, hit rebuild, and the
  page updates, with mouse and touch both working on the round mask. Record the
  `index.wasm` byte size in the PR, because the scorecard above could not cite one.

### PoC-2 (de-risks v2) -- Orb firmware boots in QEMU on the host

- Build one Orb image with the panel behind a build flag that selects
  [`esp_lcd_qemu_rgb`](https://components.espressif.com/components/espressif/esp_lcd_qemu_rgb)
  instead of the real `esp_lcd` panel.
- `idf.py qemu --graphics monitor`, confirm the framebuffer shows the same screen as PoC-1.
  This is the load-bearing check: if the two disagree, the `esp_lcd` abstraction is leaking
  and v2 will be a lie.
- Then, cheapest first: pull Velxio's image, run its ESP32 board, confirm its framebuffer
  reaches a canvas over its own WebSocket. Do **not** attempt the wasm port.
- **Pass condition:** a screenshot of the same screen from both paths, byte for byte, plus
  a written answer to "is the display bound through `esp_lcd` only?".

### PoC-3 (BLE and audio) -- real Orb, real radio

- Web Serial to flash and drive an Orb over USB CDC, streaming its display to the same
  canvas with the CastMCU command-stream shape
  ([CastMCU](https://github.com/Akhil-Chaturvedi/CastMCU)), plus Web Bluetooth to read the
  GATT service the page just designed.
- **Pass condition:** a browser that can talk to a physical Orb's GATT table. If this does
  not work in a non-Chromium browser, that is the finding, and it goes in the UI copy.

## 8. Open questions for the card

1. **What does Orb Studio emit today**, and how separable is the UI layer from the chip
   drivers? PoC-1 is roughly a day if the answer is "LVGL, cleanly", and a week if the
   answer is "one big Arduino sketch". This single question moves the estimate more than
   any other.
2. **Does Orb firmware talk to its display through `esp_lcd`?** If not, v2 needs a
   `esp_lcd` shim for the real panel first, and that is a firmware change.
3. **Is the taOS host allowed to run a long-lived QEMU process per preview session?** That
   is a packaging and resources question for the desktop app, and it is the real cost of
   v2. If the answer is no, v2 becomes Velxio-in-Docker or it does not happen.
4. **Who owns Orb firmware's CI screenshot regressions today?** If nobody, Wokwi's
   `--screenshot-part` flow is the template and our own QEMU render is the future oracle.

## Appendix: every URL cited, grouped

**Official upstream docs**
[LVGL browser](https://lvgl.io/docs/open/integration/pc/browser) ·
[LVGL SDL driver](https://lvgl.io/docs/open/integration/pc/sdl) ·
[LVGL Pro EULA](https://lvgl.io/pro/legal/eula) ·
[LVGL Pro pricing](https://lvgl.io/pro/pricing) ·
[ESP-IDF QEMU](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/qemu.html) ·
[ESP-IDF host apps](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/host-apps.html) ·
[ESP-IDF Wokwi](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/third-party-tools/wokwi.html) ·
[esp_lcd_qemu_rgb](https://components.espressif.com/components/espressif/esp_lcd_qemu_rgb) ·
[espressif/qemu repo](https://github.com/espressif/qemu/) ·
[espressif/qemu README](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md) ·
[qemu/esp32 README](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/esp32/README.md) ·
[Emscripten pthreads](https://emscripten.org/docs/porting/pthreads.html) ·
[Emscripten licence](https://emscripten.org/docs/introducing_emscripten/emscripten_license.html) ·
[Web Serial (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Web_Serial_API) ·
[Web Bluetooth (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API) ·
[WICG/serial](https://github.com/WICG/serial/blob/main/README.md) ·
[Chrome Web Serial](https://developer.chrome.com/docs/capabilities/serial) ·
[NimBLE socket transport](https://github.com/apache/mynewt-nimble/blob/master/nimble/transport/socket/src/ble_hci_socket.c) ·
[NimBLE transport doc](https://github.com/apache/mynewt-nimble/blob/master/nimble/doc/transport.md) ·
[esp-hosted UART HCI walkthrough](https://github.com/espressif/esp-hosted-mcu/blob/main/examples/host_nimble_bleprph_host_only_uart_hci/tutorial/bleprph_host_only_walkthrough.md)

**Emulators**
[ktock/qemu-wasm](https://github.com/ktock/qemu-wasm) ·
[qemu-wasm demo page](https://ktock.github.io/qemu-wasm-demo/) ·
[FOSDEM 2025 talk](https://fosdem.org/2025/schedule/event/fosdem-2025-6290-running-qemu-inside-browser/) ·
[ktock/qemu-wasm-sample](https://github.com/ktock/qemu-wasm-sample) ·
[qemu-wasm patch series](https://lists.libreplanet.org/archive/html/qemu-devel/2025-04/msg01114.html) ·
[pebble-qemu-wasm](https://github.com/ericmigi/pebble-qemu-wasm) ·
[Cirkit ESP32-S3 sim](https://www.cirkitdesigner.com/esp32-simulator) ·
[Cirkit blog](https://www.cirkitdesigner.com/blog/2026-05-05-esp32-s3-simulator) ·
[TinyEMU](https://bellard.org/tinyemu/) ·
[nuttx-tinyemu](https://github.com/lupyuen/nuttx-tinyemu) ·
[TinyEMU-in-browser writeup](https://lupyuen.github.io/articles/tinyemu2) ·
[Wokwi CLI](https://github.com/wokwi/wokwi-cli) ·
[Wokwi CLI usage](https://docs.wokwi.com/wokwi-ci/cli-usage) ·
[Wokwi CI](https://docs.wokwi.com/wokwi-ci/getting-started) ·
[Wokwi ToS](https://wokwi.com/legal/terms) ·
[Wokwi copyright](https://wokwi.com/legal/copyright) ·
[Wokwi pricing](https://wokwi.com/pricing)

**Browser-native simulators and designers**
[lv_web_emscripten](https://github.com/lvgl/lv_web_emscripten) ·
[lv_port_pc_vscode](https://github.com/lvgl/lv_port_pc_vscode) ·
[lv_port_pc_eclipse](https://github.com/lvgl/lv_port_pc_eclipse) ·
[lv_port_linux](https://github.com/lvgl/lv_port_linux) ·
[web-simulator-for-lvgl](https://github.com/airpocket-soundman/web-simulator-for-lvgl) ·
[LVGL-Web-Emulator](https://github.com/ZhangKeLiang0627/LVGL-Web-Emulator) ·
[LGFXScreenBuilder](https://github.com/tanakamasayuki/LGFXScreenBuilder) ·
[GFX_SIM (CC BY-NC)](https://59jag59.github.io/Simulateur-arduino_gfx/) ·
[Arduino_TFT_simulator](https://github.com/mdmmt05/Arduino_TFT_simulator) ·
[CastMCU](https://github.com/Akhil-Chaturvedi/CastMCU) ·
[ArduinoFake](https://github.com/fabiobatsilva/arduinofake) ·
[SquareLine](https://squareline.io/) ·
[SquareLine pricing](https://squareline.io/pricing/licenses) ·
[SDL and WebAssembly](https://discourse.libsdl.org/t/sdl-and-webassembly/24611)

**Multi-board simulators**

All Velxio links below point at the **canonical** tree,
`github.com/davidmonterocrespo24/velxio`. `github.com/velxio/velxio` is a *fork* of it and
is deliberately **not** cited as a source anywhere in this note; see section 4.1.

[Velxio, canonical](https://github.com/davidmonterocrespo24/velxio) ·
[Velxio emulator.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/emulator.md) ·
[Velxio ESP32_EMULATION.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/ESP32_EMULATION.md) ·
[Velxio ESP32_WIFI_BLUETOOTH.md](https://github.com/davidmonterocrespo24/velxio/blob/master/docs/ESP32_WIFI_BLUETOOTH.md) ·
[Velxio canonical commits (HEAD check)](https://github.com/davidmonterocrespo24/velxio/commits/master.atom) ·
[Velxio fork commits (HEAD check)](https://github.com/velxio/velxio/commits/master.atom) ·
[lcgamboa/qemu](https://github.com/lcgamboa/qemu) ·
[Espressif blog on Velxio](https://developer.espressif.com/blog/2026/07/velxio-browser-based-esp32-simulation/) ·
[arduino-esp32 2.x to 3.0 migration guide](https://docs.espressif.com/projects/arduino-esp32/en/latest/migration_guides/2.x_to_3.0.html) ·
[wayvnc](https://github.com/any1/wayvnc) ·
[wayvnc README](https://github.com/any1/wayvnc/blob/master/README.md) ·
[BTstack README](https://github.com/bluekitchen/btstack/blob/master/README.md) ·
[BTstack in the browser](https://bluekitchen-gmbh.com/bluetooth-unleashed-classic-ble-and-le-audio-in-the-browser/) ·
[webbluetooth](https://github.com/thegecko/webbluetooth/)

**Licences**
[FSF licence list](https://www.gnu.org/licenses/license-list.html) ·
[LVGL LICENCE.txt](https://raw.githubusercontent.com/lvgl/lvgl/master/LICENCE.txt) ·
[lv_web_emscripten LICENSE](https://raw.githubusercontent.com/lvgl/lv_web_emscripten/master/LICENSE) ·
[web-simulator-for-lvgl LICENSE](https://raw.githubusercontent.com/airpocket-soundman/web-simulator-for-lvgl/main/LICENSE) ·
[LGFXScreenBuilder LICENSE](https://raw.githubusercontent.com/tanakamasayuki/LGFXScreenBuilder/main/LICENSE) ·
[CastMCU LICENSE](https://raw.githubusercontent.com/Akhil-Chaturvedi/CastMCU/main/LICENSE) ·
[ArduinoFake LICENSE](https://raw.githubusercontent.com/fabiobatsilva/arduinofake/master/LICENSE) ·
[wokwi-cli LICENSE](https://raw.githubusercontent.com/wokwi/wokwi-cli/main/LICENSE) ·
[espressif/qemu esp32.c header](https://raw.githubusercontent.com/espressif/qemu/esp-develop/hw/xtensa/esp32.c) ·
[Velxio LICENSE, canonical tree](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/LICENSE) ·
[Velxio COMMERCIAL_LICENSE.md, canonical tree](https://raw.githubusercontent.com/davidmonterocrespo24/velxio/master/COMMERCIAL_LICENSE.md) ·
[taOS dependency licences](../../docs/dependency-licences.md)

**Measured artifact**
[`nuttx-tinyemu/docs/riscvemu64-wasm.wasm`](https://raw.githubusercontent.com/lupyuen/nuttx-tinyemu/main/docs/riscvemu64-wasm.wasm)
is the only payload size in this note that was measured rather than read off a README;
see the command in section 2.4. It is a **64-bit RISC-V** emulator, so treat 221,309 bytes as
an unrelated-ISA upper bound and not as an Orb estimate.
