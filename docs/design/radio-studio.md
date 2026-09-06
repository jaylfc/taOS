# Radio Studio — AI-assisted SDR / Radio Analysis Studio

**Date:** 2026-08-09
**Status:** Draft
**Amended:** 2026-08-09 — initial design spec derived from the community
'Sparky' setup (Hermes agent + HackRF) and the taOS studio pattern.

## Reference

- Pattern source: https://github.com/h00nigan/sparky-setup-guide/blob/master/Sparky-Setup-Guide.md
  (Hermes agent, HackRF CLI tools, swept spectrum, continuous scan, alert-on-new-signal,
  high-res 'staring' analysis, ATC audio playback)
- Existing taOS studio pattern: `desktop/src/apps/codingstudio/`, `designstudio/`,
  `musicstudio/` (registered in `desktop/src/registry/app-registry.ts`, tier 5 optional)
- taOS skills system: `docs/design/skills-plugins.md`, `tinyagentos/skills.py`
- Hardware auto-detect pattern: `tinyagentos/hardware.py`, `docs/design/hailo-llm-backend.md`
- Container USB passthrough: `tinyagentos/containers/lxc.py`, `tinyagentos/containers/docker.py`

## Overview

Radio Studio is an **optional, tier 5 studio app** (like Coding Studio, Design Studio)
that turns taOS into an AI-assisted SDR / radio analysis workstation. It follows the
established studio pattern: a canvas view, supporting panels, a scan log, a signal
library, an alert feed, and an agent chat pane beside the canvas.

The target workflow mirrors the community 'Sparky' setup:

1. Detect an SDR device (HackRF One, RTL-SDR, Airspy, SDRplay) connected via USB.
2. Run a spectrum survey / sweep from within the app, driven by an assigned agent.
3. Surface results in a live waterfall and spectrum view.
4. Persist detected signals in a signal library.
5. Alert the user (and the assigned agent) when a new signal appears.
6. Let the agent drive follow-up analysis: targeted sweeps, audio capture, demodulation.

The default posture is **receive-only analysis**. Decoding / transmitting is out of scope
for Phase 1 and is gated behind explicit user opt-in and jurisdiction checks.

## Goals

- Make SDR a first-class taOS hardware class, auto-detected at boot and on USB insert,
  alongside NPU, GPU, and disk.
- Map rf-scanning operations to the canonical taOS skill model so any supported agent
  framework can drive them.
- Ship a studio app that a user can install from the Store and open in one click.
- Alert the user when a previously unseen signal appears in a watched band.

## Non-goals

- Transmit capability. Radio Studio ships receive-only. TX requires separate legal
  review, licensing checks, and hardware enforcement (e.g. HackRF TX enable flag).
- Decoding / demodulation in Phase 1. Signal detection and classification only.
  Demodulation (FM, AM, digital modes) is a follow-up.
- Signal fingerprinting / library sharing between taOS instances.
- Multi-SDR load balancing or networked SDR pools.
- Mobile / tablet UI. Studio apps are desktop-first.

## Hardware: SDR Auto-Detection

### Detection model

SDR detection follows the same zero-touch pattern as NPU / Hailo detection
(`tinyagentos/hardware.py`). A new `SdrInfo` dataclass is added:

```python
@dataclass
class SdrInfo:
    type: str = ""         # hackrf | rtl-sdr | airspy | sdrplay | soapy | unknown
    device: str = ""       # /dev/bus/usb/... or sysfs path
    serial: str = ""       # device serial when available
    driver: str = ""       # soapy, hackrf, rtl-sdr, etc.
    max_sample_rate: int = 0
```

`HardwareProfile` gains an `sdr: SdrInfo = field(default_factory=SdrInfo)` attribute
and `profile_id` gains an `-sdr` suffix when an SDR is present (e.g.
`x86-cuda-16gb-sdr`).

### Detection methods

| Device | Primary probe | Fallback / notes |
|---|---|---|
| HackRF One | `hackrf_info` CLI returns `Found HackRF One` | `lsusb -d 1d50:6049`, `/sys/bus/usb/devices/*/idVendor` + `idProduct` |
| RTL-SDR | `rtl_test -d 0` succeeds | `lsusb -d 0bda:2838` |
| Airspy | `airspy_info` CLI | `lsusb` vendor/product match |
| SDRplay | `sdrplay_apiService` running or `mirsdri` binary | Windows/macOS path, not the primary Linux target |
| SoapySDR | `SoapySDRUtil --find` returns devices | Universal fallback for any Soapy-supported device |

### Runtime re-detection

Like USB storage, SDRs can be hot-plugged. The existing hardware detection loop
(or a new USB watcher thread) re-runs `detect_hardware()` on udev `add/remove`
events for USB devices. When a new SDR appears, the OS surfaces a notification
and the Radio Studio app offers to open.

### Container passthrough

The agent container must see the raw USB device. Two backends:

**LXC / Incus** (`tinyagentos/containers/lxc.py`):
```
incus config device add <container> sdr0 usb \
  vendorid=0x1d50 productid=0x6049
```

**Docker / Podman** (`tinyagentos/containers/docker.py`):
```
docker run --device=/dev/bus/usb/...
```

The container runtime backend exposes a new `usb_devices` argument on
`create_container()` (mirroring the existing `mounts` argument). The SDR
detector returns the matching `vendorid` / `productid` and the app orchestrator
adds the device when deploying an agent that has rf-scanning skills assigned.

## Agent Framework Support

### Hermes

The community 'Sparky' setup uses Hermes as the radio agent. taOS already supports
Hermes (installer: `tinyagentos/scripts/install_hermes.sh`, bridge adapter in
`docs/design/framework-agnostic-runtime.md`). rf-scanning skills register as
Hermes functions via the same adapter path used for `web_search`, `browser_control`,
etc.

Hermes skill injection uses the Hermes `functions` config key. The Skill Injector
(`docs/design/skills-plugins.md §Skill Injector`) maps each assigned rf-scanning
skill's `tool_schema` into Hermes's function-calling format.

### Hermes ARM / prisma blocker

**Issue:** Hermes (and other frameworks that use Prisma for session / memory
storage) cannot start on ARM hosts (Pi, RK3588) where Prisma does not ship a
compatible `libquery-engine` binary. This is a known upstream gap, not a taOS bug.

**Workaround today:** taOS already falls back to the shared LiteLLM master key
on ARM hosts that cannot run Prisma (`TAOS_DISABLE_AGENT_MASTER_KEY_FALLBACK=1`
to opt out). Radio Studio agents on ARM hosts should:

- Default to OpenClaw or SmolAgents (no Prisma dependency) for rf-scanning tasks.
- Surface a banner in the studio: "Hermes is unavailable on this ARM host
  (Prisma engine missing). Use OpenClaw or SmolAgents for the radio agent."
- Continue to support Hermes on x86 hosts where Prisma works.

**Longer-term fix:** Track upstream Prisma ARM support or replace the session
store with SQLite / QMD so all frameworks work everywhere. This is out of scope
for the Radio Studio spec but must be noted in any Hermes-facing documentation.

### Other frameworks

| Framework | rf-scanning skill support | Notes |
|---|---|---|
| Hermes | adapter | Works on x86. ARM blocked by Prisma. |
| OpenClaw | adapter | Recommended default on ARM. |
| SmolAgents | adapter | Recommended default on ARM. |
| PocketFlow | adapter | Skills become callable nodes. |
| Langroid | adapter | Tool registration. |
| OpenAI Agents SDK | adapter | Function tool injection. |

## rf-scanning Skills

Skills follow the canonical taOS ops-skills pattern
(`docs/design/skills-plugins.md`, `app-catalog/plugins/<id>/manifest.yaml`).

### Skill manifest format

```yaml
id: hackrf-spectrum-survey
name: HackRF Spectrum Survey
type: plugin
version: 1.0.0
category: comms
description: "Sweep a frequency band with HackRF and return power measurements"

requires:
  ram_mb: 0
  hardware: [sdr]
  cli_tools: [hackrf_sweep]

install:
  method: script
  script: scripts/install-hackrf-tools.sh
  module: tinyagentos.tools.rf_scanning.hackrf_spectrum_survey

tool_schema:
  name: hackrf_spectrum_survey
  description: "Sweep a frequency range and return FFT bins with power levels"
  input_schema:
    type: object
    properties:
      start_hz:
        type: integer
        description: "Start frequency in Hz"
      stop_hz:
        type: integer
        description: "Stop frequency in Hz"
      gain:
        type: integer
        description: "LNA / VGA gain"
      bin_width_hz:
        type: integer
        default: 100000
    required: [start_hz, stop_hz]

frameworks:
  hermes: adapter
  openclaw: adapter
  smolagents: adapter
  pocketflow: adapter
  langroid: adapter
  openai-agents-sdk: adapter

hardware_tiers:
  x86-cuda-16gb: full
  arm-npu-8gb: full
  cpu-only: full
```

### Phase 1 skills

Only one skill ships in Phase 1:

| Skill ID | Description | CLI dependency |
|---|---|---|
| `hackrf-spectrum-survey` | Sweep a frequency range, return FFT bins with timestamps and power levels | `hackrf_sweep` |

Phase 2+ skills (out of scope for this spec):

| Skill ID | Description |
|---|---|
| `hackrf-targeted-sweeps` | Targeted sweep around a known frequency with higher resolution |
| `sdr-audio-capture` | Capture baseband / demodulated audio to WAV |
| `sniffing` | Narrow-band capture for protocol analysis (e.g. 433 MHz OOK, sub-GHz) |

### CLI tool installation

`hackrf_sweep` ships in the `hackrf` package on Debian/Ubuntu. A taOS install
script (`scripts/install-hackrf-tools.sh`) checks for the binary and installs
it via `apt install hackrf`. On ARM hosts it warns that HackRF USB 3.0 throughput
may be limited by the host controller.

## Radio Studio App

### Registration

```typescript
// desktop/src/registry/app-registry.ts
{
  id: "radio-studio",
  name: "Radio Studio",
  icon: "radio",
  category: "studio",
  component: () => import("@/apps/RadioStudioApp").then((m) => ({ default: m.RadioStudioApp })),
  defaultSize: { w: 1200, h: 800 },
  minSize: { w: 800, h: 600 },
  singleton: true,
  pinned: false,
  launchpadOrder: 13.33,
  optional: true,
  tier: 5
}
```

### File structure

```
desktop/src/apps/RadioStudioApp/
  RadioStudioApp.tsx          # Top-level studio shell
  types.ts                    # Signal, Scan, Alert, SpectrumSnapshot
  useRadioStore.ts            # Local state (signals, alerts, scan log)
  SpectrumView.tsx            # Waterfall + spectrum canvas (WebGL / Canvas 2D)
  ScanLog.tsx                 # Chronological scan log table
  SignalLibrary.tsx           # Saved signals / bookmarks
  AlertFeed.tsx               # Notifications + alert list
  AgentChat.tsx               # Agent chat pane beside canvas
  SdrStatusBar.tsx            # Device status, sample rate, gain, USB link speed
  api.ts                      # Backend API calls (/api/radio-studio/*)
```

### Layout

```
+------------------------------------------------------------------+
| Radio Studio — SDR                                              _ X|
+------------------------------------------------------------------+
| Spectrum View (canvas)          | Scan Log    | Signal Library     |
| - Waterfall (time vs freq)      | - Timestamp | - Saved signals    |
| - Spectrum line (current FFT)   | - Freq span | - Notes / tags     |
| - Cursor / click to tune        | - Peaks     | - Demod actions    |
|                                  | - Agent log |                   |
+----------------------------------+-------------+-------------------+
| Alert Feed (bottom strip)                                        |
| [NEW] 144.390 MHz APRS — first seen 2s ago          [Dismiss]   |
+------------------------------------------------------------------+
| Agent Chat                                                       |
| Sparky: Running survey 1 MHz - 6 GHz...                          |
| > alert-on-new-signal --band 118-137 MHz --threshold -60dBm     |
+------------------------------------------------------------------+
```

### Views

**Spectrum View (primary canvas)**
- Waterfall: frequency on X axis, time scrolling down Y axis, colour = power (dBm).
- Spectrum line: latest FFT drawn as a line chart overlaid on the waterfall.
- Click / drag to set a new scan range. Double-click to center and zoom.
- Cursor readout: frequency, power, mode guess (AM / FM / narrow-band).

**Scan Log**
- Each scan run produces one entry: timestamp, span, step, gain, peak count.
- Clicking an entry re-runs the scan or loads the cached result.
- Agent-driven scans are tagged with the agent slug that triggered them.

**Signal Library**
- User or agent bookmarks a signal: frequency, bandwidth, modulation guess,
  first-seen, last-seen, notes.
- Persisted in project files under `projects/<project-slug>/files/signals/`.

**Alert Feed**
- System notifications (desktop bell) plus in-app strip.
- "New signal" alert: frequency appeared in a watched band that was previously
  empty or below threshold.
- Configurable threshold, debounce (avoid alerting on the same carrier 60 times
  per second), and muted frequencies.

**Agent Chat**
- Standard chat pane, scoped to the studio's agent.
- Slash commands: `/survey 1-6G`, `/stare 144.39M`, `/alert 118-137M -60`,
  `/capture 30s 144.39M`.
- Agent can push spectrum snapshots and signal cards into the chat as images /
  structured data.

### Backend routes

```
GET  /api/radio-studio/status             — SDR connected? model? sample rate?
POST /api/radio-studio/survey             — run hackrf_spectrum_survey skill
GET  /api/radio-studio/survey/{id}        — cached result (FFT bins, peaks)
POST /api/radio-studio/alert/watch        — add frequency band to watch list
DELETE /api/radio-studio/alert/watch/{id} — remove band
GET  /api/radio-studio/alerts             — recent new-signal alerts
GET  /api/radio-studio/signals            — saved signal library
POST /api/radio-studio/signals            — save / bookmark signal
```

## Receive-only Defaults + Jurisdiction Disclaimer

### Default posture

Radio Studio ships in **receive-only** mode:

- TX is disabled in the UI and the backend rejects any skill call with a
  `transmit` flag.
- The HackRF CLI is invoked without `-t` (TX) arguments. `hackrf_transfer` is
  never called by any Phase 1 skill.
- An SDR device is opened with read-only intent. The container / host policy
  enforces this via capability restrictions (no `CAP_NET_ADMIN`, no raw socket
  creation, no TX buffer writes).

### Jurisdiction disclaimer

Radio spectrum is regulated. The user is solely responsible for complying with
local laws:

- Receiving certain signals may be restricted (e.g. encrypted services,
  emergency services, aviation band in some jurisdictions).
- Transmitting without a license is illegal in most countries.
- The software does not decrypt, decode, or otherwise process payloads in
  Phase 1. Classification is limited to signal presence, bandwidth, and
  modulation heuristics.

The Store listing, installer, and in-app onboarding all display:

> Radio Studio is a receive-only analysis tool. You are responsible for
> complying with local regulations governing radio reception and transmission.
> No decoding or payload processing is performed in Phase 1.

### Analysis focus

Phase 1 is explicitly scoped to **spectrum awareness**:

- What is active?
- Where are the peaks?
- Is there a new signal?
- How does activity change over time?

Demodulation, decoding, protocol identification, and payload inspection are
explicitly out of scope for Phase 1 and are not represented in the UI or skills.

## Phase 1 Cut

### What ships

| Feature | Detail |
|---|---|
| SDR detection | `SdrInfo` in `HardwareProfile`; USB hot-plug re-detection |
| USB passthrough | LXC `usb` device add + Docker `--device`; wired into agent deploy |
| One survey skill | `hackrf-spectrum-survey` (`hackrf_sweep`) |
| Spectrum snapshot | Waterfall + FFT line in the studio canvas |
| Alert on new signal | Watched band monitor; new carrier > threshold triggers alert + notification |
| Agent chat | Standard studio chat pane; slash commands for radio ops |

### What does not ship in Phase 1

- Targeted sweeps, audio capture, sniffing skills.
- Signal demodulation or decoding.
- Hermes on ARM (Prisma blocker — use OpenClaw / SmolAgents).
- Multi-SDR support (one device per host).
- RTL-SDR / Airspy backend support (HackRF is the reference device; others
  follow the same detection + passthrough pattern).
- Cross-instance signal library sync.

### Success criteria

- A user plugs in a HackRF One, opens Radio Studio, and sees the device status
  bar show `HackRF One / 20 MSPS`.
- The user runs a survey (via agent chat or UI button) and sees a waterfall
  populate within seconds.
- Adding a band to the watch list produces a desktop notification when a new
  carrier appears.

## Architecture

```
Desktop (React)
  └── Radio Studio app (desktop/src/apps/RadioStudioApp/)
        ├── SpectrumView.tsx      — waterfall + FFT canvas
        ├── ScanLog.tsx           — scan history
        ├── SignalLibrary.tsx     — bookmarks
        ├── AlertFeed.tsx         — new-signal alerts
        └── AgentChat.tsx         — Hermes / OpenClaw chat

Backend (FastAPI)
  └── /api/radio-studio/*
        └── skill dispatch → hackrf-spectrum-survey skill

Skill runtime
  └── tinyagentos/tools/rf_scanning/hackrf_spectrum_survey.py
        └── subprocess hackrf_sweep → parse CSV → return FFT bins

Hardware
  └── tinyagentos/hardware.py::detect_hardware()
        └── _detect_sdr() → SdrInfo (vendor/product, serial, driver)

Container
  └── LXC / Docker backend
        └── USB device passthrough (vendorid/productid)
```

## File Map (Phase 1)

```
desktop/src/apps/RadioStudioApp/
  RadioStudioApp.tsx
  types.ts
  useRadioStore.ts
  SpectrumView.tsx
  ScanLog.tsx
  SignalLibrary.tsx
  AlertFeed.tsx
  AgentChat.tsx
  SdrStatusBar.tsx
  api.ts

tinyagentos/
  hardware.py                          # + SdrInfo, _detect_sdr()
  containers/
    lxc.py                             # + usb device support
    docker.py                          # + --device passthrough
  tools/
    rf_scanning/
      __init__.py
      hackrf_spectrum_survey.py        # Phase 1 skill implementation
  routes/
    radio_studio.py                    # /api/radio-studio/* routes

app-catalog/plugins/
  hackrf-spectrum-survey/
    manifest.yaml

scripts/
  install-hackrf-tools.sh              # hackrf_sweep, hackrf_info

tests/
  test_hardware.py                     # + SDR detection tests
  test_radio_studio.py                 # API + skill tests
  test_rf_scanning.py                  # hackrf_spectrum_survey unit tests
```

## Dependencies

- `hackrf` (Debian/Ubuntu package) — provides `hackrf_sweep`, `hackrf_info`.
- Existing taOS skill runtime, container backends, hardware detection, and
  desktop shell (no new framework dependencies).

## Risks

- **HackRF USB 3.0 throughput on ARM hosts.** The Pi 4/5 USB controller may
  struggle with 20 MSPS sustained. Phase 1 gates on a warning; Phase 2 can
  add sample-rate throttling.
- **Prisma ARM blocker** prevents Hermes on Pi / RK3588. Workaround is to
  default to OpenClaw / SmolAgents on ARM. Long-term fix is upstream.
- **Legal / regulatory.** The receive-only default and jurisdiction disclaimer
  mitigate but do not eliminate risk. Store listing and installer must carry
  the disclaimer prominently.
