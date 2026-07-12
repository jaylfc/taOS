# Hailo-10H LLM Backend: Auto-Detect + Managed hailo-ollama (Design)

**Status:** Approved design (2026-07). Tracking: issue #1771, board task #194.
**Target hardware:** Raspberry Pi 5 + Hailo-10H on the AI HAT+2 (M.2, PCIe, 40 TOPS).

## Why

The Hailo-10H is an LLM-capable NPU. A Raspberry Pi 5 with the AI HAT+2 can run
real chat models on the accelerator through `hailo-ollama`, Hailo's
Ollama-compatible GenAI server. This is confirmed working in the field: the
community tester on #1771 (@doc62fr) runs firmware 5.1.0, has 5 working chat
models, and has `hailo-ollama` answering on its upstream default of
`0.0.0.0:8000`.

taOS today does half the job. Runtime hardware detection already distinguishes
the 10H (`tinyagentos/hardware.py` lines 288 to 293 return
`NpuInfo(type="hailo10h", tops=40)` via `/dev/hailo*` plus `lspci -d 1e60:`),
but nothing acts on it:

- No installer path exists. `install.sh`, `scripts/install-server.sh`, and
  `scripts/install-worker.sh` all gate on `/dev/rknpu` for Rockchip (the
  `install.sh` block at line 182, the chained `TAOS_RKNPU_SETUP=1` opt-in in
  the server and worker installers) but have no Hailo equivalent. A Hailo host
  installs as CPU-only and the accelerator sits idle.
- The controller only notices the accelerator after it starts (the exact gap
  @doc62fr reported), and even then there is no backend to route to.

The RK3588 path solved this end to end: `/dev/rknpu` gates
`scripts/install-rknpu.sh`, which installs rkllama on the taOS port 7833 as a
managed `rkllama.service`, registered through the backend-service framework
(`docs/design/cluster-backend-service-management.md`,
`tinyagentos/cluster/backend_services.py`) and exposed as an Ollama-compatible
provider (`OllamaCompatAdapter` in `tinyagentos/backend_adapters.py`).

**This design mirrors that path for the Hailo-10H, component for component.**
Same detection-gates-installer pattern, same managed systemd service contract,
same Ollama-compatible provider shape, different device node, service name,
and port.

## Non-goals

- **On-device model compilation.** `.hef` (Hailo Executable Format) models are
  produced offline with the Hailo Dataflow Compiler on an x86 PC. v1 ships a
  small catalog of known-good prebuilt `.hef` chat models only; compiling new
  ones is out of scope.
- **Hailo-8 / 8L LLM support.** The 8L (13 TOPS) is a vision accelerator with
  no LLM runtime. Detection must exclude it from the LLM install path.
- **Vision pipelines.** Camera/vision workloads on Hailo are the optional
  Store track (section G), not this backend.
- **Non-systemd platforms.** Same boundary as the backend-service framework:
  v1 is Linux + systemd (Raspberry Pi OS).
- **Multiple Hailo devices per host.** One `/dev/hailo0` assumed in v1.

## Hardware compatibility note (must appear in user-facing docs)

The AI HAT+2 occupies the Pi 5's only M.2 slot, the same slot normally used
for an NVMe SSD. A user cannot have Hailo LLM acceleration and NVMe storage at
the same time. The supported combination for Hailo LLM plus fast storage is
Hailo in the M.2 slot and a USB SSD for storage. The installer and the
Store listing must both carry this note so nobody buys the HAT expecting to
keep their NVMe boot drive.

RAM tiers: Pi 5 ships in 4/8/16 GB variants. With `npu.type != "none"` the
hardware profile becomes `arm-npu-<ram>gb` (see `HardwareProfile.profile_id`),
so catalog `hardware_tiers` entries for this backend key on `arm-npu-8gb` and
`arm-npu-16gb`.

## Architecture

### What already exists (audit, reuse as-is)

| Piece | RK3588 today | Hailo today |
|---|---|---|
| Runtime NPU detection | `hardware.py::_detect_npu()` returns `rknpu` | Already returns `hailo10h` (40 TOPS) vs `hailo` (8L, 13 TOPS) |
| Install-time gate | `/dev/rknpu` in `install.sh:182`, `install-server.sh`, `install-worker.sh` | **Missing** |
| Installer script | `scripts/install-rknpu.sh` | **Missing** (`scripts/install-hailo.sh`, new) |
| Backend runtime | rkllama on 7833 | **Missing** (hailo-ollama on 7836, new) |
| Managed service | `rkllama.service`, manifest `lifecycle.auto_manage: true` | **Missing** (`hailo-ollama.service`, new manifest) |
| Worker probe | `("rkllama", "http://localhost:7833")` in `detect_backends()` | **Missing** (new candidate on 7836) |
| Provider adapter | `ADAPTERS["rkllama"] = OllamaCompatAdapter()` | **Missing** (same adapter class, new key) |
| Model format | `.rkllm` catalog manifests | **Missing** (`.hef` catalog manifests, new) |

### A. Detection

**Install time (the gap this design closes).** The device node for the 10H on
the AI HAT+2 is `/dev/hailo0` (PCIe M.2, driver from the `hailo-all` /
HailoRT packages). Three installers gain a Hailo gate mirroring their existing
RKNPU gate:

1. `install.sh` (next to the `/dev/rknpu` block at line 182): if
   `/dev/hailo0` exists and the device identifies as a 10H, print the
   detection notice and the `scripts/install-hailo.sh` invocation, and chain
   into it when `TAOS_HAILO_SETUP=1`.
2. `scripts/install-server.sh`: mirror the `TAOS_RKNPU_SETUP` chained
   auto-install (line 422 region) with `TAOS_HAILO_SETUP`.
3. `scripts/install-worker.sh`: mirror the RKNPU worker gate (line 986
   region): warn when the device is present but hailo-ollama is missing,
   chain into `install-hailo.sh` under `TAOS_HAILO_SETUP=1`, and never fail
   the worker install if the chained install fails.

**10H vs 8L discrimination.** The 8L is vision-only and must never trigger the
LLM installer. Discrimination uses the same rule `hardware.py` already applies:
`lspci -d 1e60:` output containing `10h` or `hailo-10` means 10H; anything
else with a `/dev/hailo*` node is treated as 8-class and gets a "vision only,
no LLM support" notice instead of the installer. Where `lspci` is inconclusive
the installer falls back to `hailortcli fw-control identify` and greps the
reported architecture. `TAOS_FORCE_HAILO=1` forces the branch for bench
setups, mirroring `TAOS_FORCE_RKNPU`.

**Runtime.** Two additions so a Hailo host registers an LLM-capable backend
instead of falling back to CPU:

- `tinyagentos/worker/agent.py::detect_backends()` gains the candidate
  `("hailo-ollama", "http://localhost:7836")` in the probe list. The probe is
  live (health + model list), consistent with the "backend-driven, no static
  declarations" rule in that function.
- `tinyagentos/scheduler/backend_catalog.py::BACKEND_CAPABILITIES` gains
  `"hailo-ollama": {"llm-chat"}`. No embedding/reranking claim in v1; extend
  only after the tester confirms those model types run.

### B. Port assignment: 7836

Upstream `hailo-ollama` listens on `0.0.0.0:8000`. Port 8000 is banned by taOS
port hygiene (`tinyagentos/installers/port_allocator.py::RESERVED_PORTS`, it
is the Django/generic-dev slot), and worse, `detect_backends()` already probes
8000 as a llama-cpp and vllm candidate, so a hailo-ollama left on 8000 would
be misclassified as one of those.

taOS therefore installs hailo-ollama remapped to **7836**, the next free slot
in the taOS service block (7832 qmd, 7833 rkllama, 7834 LiteLLM, 7835
llama-cpp). This mirrors exactly how rkllama was moved off its upstream 8080
onto 7833. Consequences encoded in the slices:

- `7836` is added to `RESERVED_PORTS` with the comment
  `# taOS hailo-ollama NPU backend`.
- The service manifest, health URL, worker probe, and systemd unit all agree
  on 7836. The installer honours `TAOS_HAILO_OLLAMA_PORT` for overrides, same
  contract as `TAOS_RKLLAMA_PORT`.
- No legacy-8000 migration shim is needed (unlike rkllama's 8080 to 7833
  move in `config.py::_migrate_legacy_rkllama_port`) because taOS has never
  seeded a hailo-ollama provider before. Fresh installs land on 7836 only.

### C. `scripts/install-hailo.sh` (new)

Mirrors the structure and safety contract of `scripts/install-rknpu.sh`:

- **Gating:** interactive confirmation, or headless via `TAOS_HAILO_SETUP=1`
  or `--yes`. Non-interactive without opt-in prints the install command and
  exits 0.
- **Environment banner:** distro, kernel, board, `hailortcli fw-control
  identify` output (device arch + firmware version), so any pasted failure
  log is self-describing.
- **Steps:**
  1. Verify `/dev/hailo0` exists and identifies as 10H (section A rule).
     8-class device: print the vision-only notice, exit 0.
  2. Ensure HailoRT + firmware: on Raspberry Pi OS prefer
     `apt install hailo-all` from the Raspberry Pi repository; otherwise
     direct the user to the Hailo Developer Zone package. Enforce a minimum
     firmware of 5.1.0, the version the community tester has confirmed
     running LLMs. Installing Hailo packages requires the user to accept
     Hailo's EULA; the script surfaces that acceptance, it never bypasses it
     (section on licensing below).
  3. Install `hailo-ollama` at a pinned ref (`TAOS_HAILO_OLLAMA_REPO` /
     `TAOS_HAILO_OLLAMA_REF` overrides, defaults pinned in the script), into
     `TAOS_HAILO_OLLAMA_DIR` (default `~<user>/hailo-ollama`).
  4. Configure it to listen on `TAOS_HAILO_OLLAMA_PORT` (default 7836). The
     exact mechanism (flag, env var, or config file) is confirmed against the
     tester's install in slice S2 before the script is finalized; see open
     questions.
  5. Install and `enable --now` a systemd unit `hailo-ollama.service`
     (system scope) whose `ExecStartPre` reaps any orphan bare process
     holding the port, same adopt-an-orphan lesson as PR #1755 for rkllama.
  6. Health-wait: poll `http://localhost:7836/api/tags` until 200 with a
     `"models"` body or a timeout, then print the summary block.
- **Safety:** `set -euo pipefail`, idempotent re-run is a no-op, sudo only
  for apt and systemd, fail-soft with actionable messages.

### D. Managed service registration

New service manifest `app-catalog/services/hailo-ollama/manifest.yaml`
following the managed-service contract from
`docs/design/cluster-backend-service-management.md` and modeled on
`app-catalog/services/rkllama/manifest.yaml`:

```yaml
id: hailo-ollama
name: hailo-ollama (Hailo-10H NPU LLM)
type: service
category: llm-runtime
description: "Ollama-compatible LLM server on the Hailo-10H NPU (Raspberry Pi 5 + AI HAT+2)"
requires:
  ram_mb: 1024
  disk_mb: 500
  ports: [7836]
install:
  method: script
  script: scripts/install-hailo.sh
hardware_tiers:
  arm-npu-8gb: full
  arm-npu-16gb: full
  cpu-only: unsupported
lifecycle:
  backend_type: hailo-ollama
  default_url: http://localhost:7836
  auto_manage: true
  unit: hailo-ollama.service
  scope: system
  health:
    url: "http://localhost:7836/api/tags"
    expect: '"models"'
  startup_timeout_seconds: 120
```

Because `lifecycle.auto_manage: true` declares `unit` + `scope` + `health`,
the backend flows through the existing framework with zero new plumbing:
`cluster/backend_services.py::load_managed_backends()` picks it up, the
worker-agent ensure/self-heal adopts orphans on heartbeat, the #1743
"Restart AI Services" recovery covers it, and the Cluster UI Backend Services
rows render it. `scripts/check_manifests.py` managed-lint gates the manifest
in CI.

### E. Provider adapter (Ollama-compatible, same shape as rkllama)

hailo-ollama speaks the Ollama API, so no new adapter class is written. The
backend type `"hailo-ollama"` is added everywhere the ollama-compatible set is
enumerated today:

- `tinyagentos/backend_adapters.py`: `ADAPTERS["hailo-ollama"] =
  OllamaCompatAdapter()` (next to the existing `rkllama` / `ollama` entries).
- `tinyagentos/litellm_config.py`: the two membership checks that currently
  read `in ("ollama", "rkllama")` (backend URL collection near line 147,
  embedding discovery near line 359) become `in ("ollama", "rkllama",
  "hailo-ollama")`. Registered models keep the `ollama/<name>` LiteLLM prefix,
  identical to rkllama.
- `tinyagentos/config.py`: when the hardware profile reports
  `npu.type == "hailo10h"`, auto-seed a provider backend named
  `local-hailo-ollama` with `type: hailo-ollama`, `url:
  http://localhost:7836`. The name follows the `local-<service-id>` rule so
  registry-first model registration matches `requires.backends[].id` from the
  model manifests. This deliberately avoids repeating the `local-npu` naming
  bug that #1710 had to migrate for rkllama.

Model registration stays registry-first (models are store-only, #1710): only
store-installed `.hef` models with a catalog manifest get LiteLLM aliases.

### F. Model catalog (`.hef`)

Models for the 10H are `.hef` files compiled offline. v1 ships catalog
manifests under `app-catalog/models/<name>-hef/manifest.yaml` for a small set
of known-good chat models, seeded from the 5 models the community tester has
working (exact list confirmed on #1771 before slice S6). Each manifest mirrors
the `.rkllm` model manifests (`app-catalog/models/qwen2.5-1.5b-rkllm/` is the
reference shape): a `variants` entry with `format: hef`, `size_mb`,
`download_url`, `sha256`, and `requires.backends: [{id: hailo-ollama}]`, plus
`hardware_tiers` keyed on `arm-npu-8gb` / `arm-npu-16gb`.

Hosting follows the mirror policy (`docs/mirror-policy.md`): `.hef` binaries
that live on third-party accounts get mirrored into a taOS-controlled
HuggingFace repo with pinned sha256, the same reason
`jaysom/tinyagentos-rockchip-mirror` exists for the RK3588 path.

Any model outside the catalog needs offline recompilation with the Hailo
Dataflow Compiler on an x86 PC. That is out of scope in v1; the Models UI
should say so rather than offering a dead end.

### G. Two-track: optional Store app, arms-length (never vendored)

Separate from and independent of the native backend, taOS offers
`gregm123456/raspberry_pi_hailo_ai_services` as an **optional** Store install
for users who want the broader Hailo service stack (vision and companion
services).

- **Arms-length only.** The project's license is NOASSERTION and it bundles
  Hailo proprietary SDK submodules. It is NEVER vendored into the taOS repo,
  no source copies, no forks shipped in-tree.
- **Pattern:** identical to the RK NPU image-generation installs (the
  `ezrknpu` / `lcm-dreamshaper-rknn` precedent): the Store manifest declares
  `install.method: script`, the script fetches from upstream at install time
  at a pinned ref, and the install flow presents a license/EULA acceptance
  step the user must confirm before anything is downloaded.
- The native backend (sections A to F) does not depend on this app in any
  way; a user can install either, both, or neither.

## Slice plan

Each slice is one PR, independently shippable, testable without Hailo
hardware (CI-level checks listed per slice), and mechanical enough for an
external coding agent to implement from this text. Slices S1, S2, S3 have no
ordering constraints between them; S4 depends on S2; S5 depends on S1; S6
depends on S3; S7 depends on all.

**S1. Port reservation + capability map.**
Files: `tinyagentos/installers/port_allocator.py` (add `7836,  # taOS
hailo-ollama NPU backend` to `RESERVED_PORTS`),
`tinyagentos/scheduler/backend_catalog.py` (add `"hailo-ollama":
{"llm-chat"}` to `BACKEND_CAPABILITIES`). Add a test asserting 7836 is never
allocated to an app (extend the existing port-allocator tests) and one
asserting the capability entry exists.
Verify: `python -m pytest tests/ -k "port_alloc or backend_catalog" -q` and
`grep -n 7836 tinyagentos/installers/port_allocator.py`.

**S2. `scripts/install-hailo.sh`.**
New file implementing section C exactly: gating, banner, 10H check, HailoRT
ensure, hailo-ollama install pinned ref, port remap to 7836, systemd unit,
health-wait. Structure, logging helpers, and safety contract copied from
`scripts/install-rknpu.sh`.
Verify: `bash -n scripts/install-hailo.sh`, `shellcheck
scripts/install-hailo.sh`, and running it on a non-Hailo host must print the
"no Hailo-10H detected" message and exit 0 without touching the system.

**S3. Service manifest.**
New file `app-catalog/services/hailo-ollama/manifest.yaml` with the exact
content of section D (adjust only if the managed-lint schema demands
additional required keys).
Verify: `python scripts/check_manifests.py` passes and `python -m pytest
tests/test_check_manifests.py tests/test_backend_services.py -q` stays green;
`load_managed_backends()` must return the new backend in a unit test using the
real manifest file.

**S4. Install-time gates.**
Files: `install.sh` (Hailo block adjacent to the `/dev/rknpu` block at line
182), `scripts/install-server.sh` and `scripts/install-worker.sh` (mirror
their RKNPU sections with `/dev/hailo0` + 10H check, `TAOS_HAILO_SETUP`
chaining into `scripts/install-hailo.sh`, fail-soft on chain failure,
`TAOS_FORCE_HAILO` override). Document the new env vars in each script's
header comment.
Verify: `bash -n` on all three scripts, `shellcheck` on the changed regions,
and `grep -n "TAOS_HAILO_SETUP\|/dev/hailo0"` shows the gate in all three.

**S5. Runtime detection + provider adapter.**
Files: `tinyagentos/worker/agent.py` (add the `("hailo-ollama",
"http://localhost:7836")` probe candidate), `tinyagentos/backend_adapters.py`
(`ADAPTERS["hailo-ollama"] = OllamaCompatAdapter()`),
`tinyagentos/litellm_config.py` (extend both ollama-compat membership checks),
`tinyagentos/config.py` (auto-seed `local-hailo-ollama` when
`npu.type == "hailo10h"`, following the existing seed path for rkllama).
Tests: extend the worker-agent backend-detection tests with a mocked
hailo-ollama on 7836; assert the seeded backend name and type; assert LiteLLM
config generation emits `ollama/<model>` entries for a hailo-ollama backend.
Verify: `python -m pytest tests/ -k "worker_agent or litellm_config or
config" -q`.

**S6. Model catalog manifests.**
New files under `app-catalog/models/` for the confirmed known-good `.hef`
chat models (list settled on #1771), each following section F and the
`qwen2.5-1.5b-rkllm` manifest shape, with mirrored `download_url` + `sha256`.
Verify: `python scripts/check_manifests.py` and the manifest-driven registry
tests stay green.

**S7. Docs + tester handoff.**
Update `docs/getting-started.md` hardware section and the AI HAT+2
compatibility note (M.2 slot conflict, USB SSD guidance), then post the
"ready to test" checklist (below) on #1771.
Verify: `python scripts/check_doc_gate.py` passes; checklist posted.

## Testing and the community-tester handoff

There is no Hailo-10H in the maintainer fleet. Every slice above is gated by
CI-level verification that needs no hardware (pytest with mocked probes,
`bash -n`, shellcheck, manifest lint). Hardware acceptance runs through the
community tester on #1771. The S7 handoff comment gives him this exact
sequence:

1. `sudo TAOS_HAILO_SETUP=1 bash scripts/install-hailo.sh` and paste the
   banner + summary output.
2. `systemctl status hailo-ollama` shows active; `curl -s
   http://localhost:7836/api/tags` returns his model list.
3. Reboot; both checks in step 2 still pass (boot persistence).
4. Fresh worker/server install on the same box: confirm the installer prints
   the Hailo detection notice without `TAOS_HAILO_SETUP` and chains with it.
5. In the taOS UI: the hardware profile shows the 40 TOPS NPU, the Cluster
   app shows `hailo-ollama.service` healthy with working Restart, and a chat
   against a catalog `.hef` model routed through LiteLLM produces output.
6. "Restart AI Services" (#1743) restarts hailo-ollama successfully.

A step failing means the slice that owns it gets a fix PR before the feature
is announced.

## Security and licensing notes

- **Hailo EULA, user-accepted at install time.** HailoRT, the firmware, and
  the Dataflow Compiler are Hailo-proprietary. taOS never redistributes them:
  the installer drives the platform package flow (`apt install hailo-all` on
  Raspberry Pi OS or the Hailo Developer Zone download) and the user accepts
  Hailo's terms there. Nothing proprietary is mirrored or committed.
- **No vendoring of NOASSERTION code.** The optional Store app (section G)
  is fetch-from-upstream at a pinned ref with an explicit license-accept
  step. Its code never enters this repository.
- **Pinned artifacts.** hailo-ollama installs at a pinned ref; `.hef` model
  downloads carry sha256 pins and go through the taOS mirror policy, so a
  changed or vanished upstream cannot silently alter what users run.
- **Port hygiene.** 7836 joins `RESERVED_PORTS` so no user app can squat the
  backend port; the upstream 8000 default is never used, which also keeps the
  llama-cpp/vllm probes on 8000 unambiguous.
- **Network exposure.** The unit binds the same way rkllama does today, and
  the core remains LAN-only by default. Cross-node access goes through the
  HMAC-authenticated worker agent, never by dialing the backend directly.

## Open questions

1. **Port remap mechanism.** How hailo-ollama's listen port is configured
   (CLI flag, env var, or config file) must be confirmed on the tester's
   install before S2 is finalized. The design assumes it is configurable;
   if a given release is not, S2 pins a release that is, or carries a
   one-line service-file override.
2. **v1 model list.** Which of the tester's 5 working models become the
   catalog set, and where their `.hef` files are mirrored from (settled on
   #1771 before S6).
3. **Firmware floor.** 5.1.0 is confirmed working. Whether older firmware
   runs LLMs is unknown; v1 enforces >= 5.1.0 and can relax later with
   evidence.
4. **8GB RAM headroom.** Host-side RAM needs of `.hef` chat models on a Pi 5
   8GB alongside the controller stack are unmeasured; `requires.ram_mb`
   values in S6 manifests may need tuning after tester feedback.
5. **Embedding/reranking on 10H.** `BACKEND_CAPABILITIES` claims only
   `llm-chat` in v1. If Hailo ships embedding-capable GenAI models, the
   capability set extends and qmd could gain a second NPU target.
