# Library-replacement and licence audit — September 2026

**Date:** 2026-09-04
**Repo:** `jaylfc/taOS`, audited at branch `exec/tsk-itxxdo` (version `1.0.0-beta.50`); every
BLOCKER and every security row re-verified against `origin/dev` before this document was written.

**The question asked:** *"audit the entire project and look for any opportunities to replace custom
code with tried and tested open source code and libraries — this could save us from edge cases
later; make sure their licensing aligns with ours too."*

---

## Method

The tree was split into five slices, each audited independently and read-only:

| Slice | Surface | Rough size |
| --- | --- | --- |
| Licences | `pyproject.toml`, `uv.lock`, `desktop/package.json`, `package-lock.json`, vendored/copied material, the project's own licence files | 172 Python packages, 926 unique npm `name@version` |
| Desktop | `desktop/src` | 950 TS/TSX files, ~172,600 LOC incl. tests |
| Tooling | `scripts/`, `install*.sh`, `.github/workflows/`, `.githooks/`, `systemd/`, `mac/`, `os-build/`, `bin/`, `cli/` | ~6,000 LOC of root-running bash + 9 CI gate scripts |
| Python core | `tinyagentos/*.py` (142 top-level modules) plus `scheduler/ events/ broker/ channel_hub/ clients/ adapters/ cli/` | 37,275 LOC |
| Python domain | the rest of `tinyagentos/**` — `routes/ projects/ cluster/ containers/ userspace/ themes/ mcp/ notes/ hub/ taosnet/ otel/ push/ middleware/ knowledge_fetchers/ chat/ …` | remainder |

Rules the auditors worked to:

- **Every licence was read from the registry or the shipped artefact, never from a README claim.**
  Python licences came from `https://pypi.org/pypi/<name>/<version>/json` (PEP 639
  `license_expression` first, then `license`, then `License ::` classifiers), cross-checked against
  the `METADATA` of the 161 packages installed in the repo `.venv`. npm licences came from
  `package-lock.json` (1024 entries) with all blanks and non-SPDX strings resolved against the live
  registry and the upstream `LICENSE` file. Anything that could not be confirmed is marked
  **(unverified)** here too.
- **A finding needs a concrete defect with a cited line, or it is labelled "no defect found —
  maintenance-only win".** Nothing was invented to fill a table. Where an auditor proved a defect by
  *executing* the real function rather than reading it, this document says **"proven by running"**.
- **Line numbers** in §1 and §3.1 are as they stand on `origin/dev` today (they drifted from the
  audit branch in a few files). Line numbers elsewhere are as recorded by the slice auditor on
  `exec/tsk-itxxdo`.

### Licence policy applied

taOS's public core is **AGPL-3.0-or-later** *plus* a separate commercial licence
(`COMMERCIAL-LICENSE.md`, `CLA.md`). A dependency must therefore be usable **both** in an AGPL
distribution **and** in a proprietary/commercial one.

| Verdict | Licences | Reasoning |
| --- | --- | --- |
| **OK** | MIT, BSD-2/3-Clause, ISC, Apache-2.0, PSF, Zlib, 0BSD, Unlicense, CC0, MPL-2.0, ZPL-2.1 | Permissive, or file-level copyleft that does not reach taOS's own files |
| **FLAG** | LGPL-2.1 / LGPL-3.0, wheels that statically bundle notice-bearing components, dual licences where one arm is unusable | Usable, but carries relink/notice obligations a commercial licensee inherits and that are currently undocumented |
| **BLOCKER** | GPL-2/3-only, AGPL, SSPL, BUSL, Elastic, CC-BY-NC, "source available", proprietary, no licence file | Cannot be conveyed under either half of the dual licence |

### Product constraints every recommendation was weighed against

Raspberry Pi 5 / Orange Pi 5 Plus, ARM64, a **4 GB host must still run the core**, offline-first,
`uv sync` installs **no extras** so every new Python dependency is paid by every install, and JS
dependencies are paid in Vite bundle bytes. Pure-Python or manylinux-aarch64-wheel only; no
compile-at-install.

---

## 1. Licence BLOCKERs

Two. Both ship to end users today.

### B1 — `tldraw` 4.5.12: source-available, production use forbidden without a paid key

**Evidence.** `desktop/package.json:40-41` pins `"@tldraw/assets": "4.5.12"` and
`"@tldraw/tldraw": "^4.5.10"`. npm reports `license: "SEE LICENSE IN LICENSE.md"` for the whole
family (`tldraw`, `@tldraw/tldraw`, `@tldraw/assets`, `@tldraw/editor`, `@tldraw/utils`,
`@tldraw/validate` — 6 packages). The licence text fetched verbatim from
`raw.githubusercontent.com/tldraw/tldraw/v4.5.12/LICENSE.md` says:

> "Production Environment" means any production deployment of the Software that operates on servers,
> cloud platforms, web applications, **or where the software is used to provide functionality to end
> users, customers, or the public**. […]
>
> **Conditions.** In exchange for these permissions, you agree:
> - **Not to use the Software in Production Environments.**
> - Not to disable, change, or interfere with the Software's License Key enforcement.
> - **Not to make the Software available under a license that supersedes or negates the effect of
>   this License.**
> - To include a verbatim copy of this License in any distribution of the Software.

`tldraw.dev/pricing` (fetched 2026-09-04) confirms **there is no free watermarked production tier
any more** — a 100-day trial, an annual SDK licence, discounted startup pricing, and an
application-gated hobby licence. The v2/v3-era "free with watermark" route is gone.

**Blast radius.** Live in the Projects canvas:

- `desktop/src/apps/ProjectsApp/canvas/CanvasBoard.tsx:2-3` — `Tldraw, Editor, createTLStore, defaultShapeUtils, TLShape` and `@tldraw/assets/urls`
- `desktop/src/apps/ProjectsApp/canvas/shapes/LinkShape.tsx:1`, `GenericShape.tsx:1`, `ImageShape.tsx:1`, `NoteShape.tsx:7`, `TextShape.tsx:7`
- Wired live at `desktop/src/apps/ProjectsApp/canvas/CanvasView.tsx:1,13` — `CanvasView` renders `CanvasBoard`, nothing else.
- `grep -rni "licenseKey" desktop/src` → **0 hits** (verified). taOS is on the unlicensed path.

**Four consequences, all live:**

1. **Terms breach.** taOS ships the canvas to end users on their own hardware — squarely "used to
   provide functionality to end users".
2. **AGPL incompatibility.** AGPL-3.0 §10 forbids imposing further restrictions on downstream
   recipients. tldraw's "no production use" and "not to make the Software available under a license
   that supersedes this License" cannot be conveyed under the AGPL. The SPA bundle as distributed is
   therefore not lawfully AGPL-licensable as a whole.
3. **Commercial licence poisoned.** `COMMERCIAL-LICENSE.md` offers the right to "embed or bundle
   taOS inside a proprietary product you distribute". That right cannot be granted over tldraw.
4. **Distribution condition unmet.** "Include a verbatim copy of this License in any distribution" —
   tldraw's `LICENSE.md` lives only in `node_modules/`, which is not checked in, and Vite does not
   emit it into `desktop/dist`.

Also logged: **`@tldraw/assets` was Apache-2.0 up to `3.12.0-canary`** and was relicensed afterwards
(verified across npm version history). Whoever added the dependency would have read Apache-2.0.

**Recommended remedy: replace with Excalidraw (MIT).** The decision was already taken and never
landed:

- `@excalidraw/excalidraw` 0.18.1 (MIT) and `@excalidraw/mermaid-to-excalidraw` 2.2.2 (MIT) are
  already installed (`desktop/package.json:20-21`).
- `desktop/src/apps/ProjectsApp/canvas/ExcalidrawBoard.tsx` already exists (125 lines) — and is
  referenced **only by its own test** (`ProjectsApp/__tests__/ExcalidrawBoard.test.tsx`).
- `desktop/src/apps/ProjectsApp/canvas/element-to-excalidraw.ts` already exists beside
  `element-to-shape.ts`.
- `desktop/src/apps/DecisionsApp.test.tsx:42,45` carries the decision record verbatim:
  `{ label: "Excalidraw", value: "excalidraw", recommended: true, rationale: "MIT licensed" }`
  against `context: "tldraw is buggy and license-incompatible."`

**Effort: M.** One `CanvasView.tsx` switch plus a shape-adapter port — the five `shapes/*.tsx`
files are tldraw `ShapeUtil` subclasses and have no Excalidraw equivalent; the
`element-to-excalidraw.ts` mapping is the replacement path. The alternative is to **buy a tldraw SDK
licence**, which fixes consequence 1 but not 2 or 3 — the AGPL half of the dual licence stays
unconveyable. **Recommendation: Excalidraw.** Either way, do not cut another release with tldraw
unlicensed.

### B2 — `litellm-enterprise` 0.1.51: proprietary, installed on every server

**Evidence.**

```
$ curl https://pypi.org/pypi/litellm-enterprise/0.1.51/json
license: None
license_expression: LicenseRef-Proprietary
classifiers: []
summary: Package for LiteLLM Enterprise features
author: BerriAI
```

It is not behind an extra of its own. `uv.lock:1301` lists it as a plain member of litellm's `proxy`
extra (`[package.optional-dependencies] / proxy = [ … { name = "litellm-enterprise" }, … ]`), with
its own package block at `uv.lock:1323`. And taOS installs exactly that on every real server:

```
scripts/install-server.sh:1508  log "installing controller python deps into .venv (pip install -e '.[proxy]')"
scripts/install-server.sh:1510  ./.venv/bin/pip install --quiet -e ".[proxy]"
.github/workflows/security.yml  .[dev,proxy,worker]
```

**Blast radius.** `grep -rn "litellm_enterprise" tinyagentos/` → **0 hits**. No taOS source imports
it, so it is inert code — but *distribution* is what a licence governs, and it is being distributed.
A commercial taOS licensee redistributing the venv would be redistributing BerriAI proprietary code.

**Recommended remedy.** `litellm` itself (1.94.2) is plain **MIT** and is fine. Pick one:

1. Drop the `[proxy]` extra from the install path and depend on plain `litellm` — cheapest if the
   proxy features in use do not need it.
2. `pip install --no-deps` the enterprise wheel out, or add a pip constraint that excludes it.
3. Isolate the LiteLLM proxy in its own venv, which also stops its 40-odd transitive packages
   (boto3, prisma, redis, rq, polars, restrictedpython, soundfile → **libsndfile LGPL-2.1**) landing
   in the taOS core venv.

**Effort: S** for options 1–2, **M** for option 3.

**A third, structural problem sits behind B2:** the real install path **bypasses `uv.lock`
entirely**. `scripts/install-server.sh:1510` runs `pip install -e ".[proxy]"`, and
`scripts/install-server.sh:1512-1515` then `pip install`s **`yt-dlp`, which is not in
`pyproject.toml` at all**. The dependency set actually installed on a production Pi is therefore not
the set audited in `uv.lock`, and a future upstream relicense reaches users without tripping the
lockfile. Any licence-scan CI job must be pointed at what the installer actually installs.

**Resolved (tsk-f3j765).** Neither option 1 nor 2 as written: pip cannot subtract one member of
another package's extra, and plain `litellm` does not run the proxy. Instead the `proxy` extra in
`pyproject.toml` inlines litellm 1.94.2's own proxy requirements minus `litellm-enterprise`, caps
litellm `<1.95` (the installer's `pip install -e .[proxy]` does not read `uv.lock`, and litellm 1.99
grows requirements the inlined list lacks), and `install-server.sh` uninstalls a copy an earlier
install left behind. `yt-dlp` is now a declared dependency. `scripts/check_install_licences.py`
walks `uv.lock` from the server extras and fails on any blocked licence or any installer
`pip install` of an undeclared package; `tests/test_install_licences.py` holds the rule.
Option 3 (isolated proxy venv) remains the structural answer to the transitive footprint, incl.
`soundfile`'s bundled libsndfile (§2.1).

---

## 2. Licence FLAGs and compliance hygiene

Nothing in this section stops a release. All of it is real obligation that a commercial licensee
would inherit undocumented, and most of it closes with one generated file plus one About panel.

### 2.1 LGPL dependencies

| Package | Version | Licence | Scope | Where |
| --- | --- | --- | --- | --- |
| `zeroconf` | 0.150.0 | **LGPL-2.1-or-later** | **core** | unconditional top-level import at `tinyagentos/services/mdns_publisher.py:29-30` |
| `pystray` | 0.19.5 | **LGPL-3.0-or-later** | `worker` extra | import guarded at `tinyagentos/worker/tray.py:23`, still shipped; installed by `scripts/taos-deploy-helper.sh:218` |
| `python-xlib` | 0.33 | **LGPL-2.1-or-later** | transitive of `pystray` on Linux | — |
| `libsndfile` (bundled inside the `soundfile` BSD-3 wheel) | — | **LGPL-2.1** | `proxy` extra | via `litellm[proxy]` |

`zeroconf` is the one that matters: it is a **core** dependency, so every install and every
commercial licensee gets it. LGPL §4/§6 relink-and-notice duties are nowhere documented.
LGPL-3 (`pystray`) additionally drags in GPLv3 §6 "Installation Information" for User Products —
directly relevant to a Pi appliance image.

Two courses, not mutually exclusive: (a) document the LGPL components and the relink offer in a
notices file and carve them out of `COMMERCIAL-LICENSE.md`; (b) note that the installer already
apt-installs `avahi-daemon` (`install.sh:40`) and `os-build/.../avahi/services/` ships an Avahi
service file — **three mDNS implementations for one feature**, one of them LGPL. Consolidating on
Avahi (a separate daemon, not linked) would remove the core LGPL dependency outright.

### 2.2 Dual licences where only one arm is usable

- **`text-unidecode` 1.3 — Artistic-1.0 OR GPL-2.0-or-later**, reaching the tree via
  `python-slugify` ← `litellm[proxy]`. Verified from `text_unidecode-1.3.dist-info/METADATA`
  (`License: Artistic License` plus GPL classifiers). Usable **only** by electing the Artistic arm,
  and that election is currently implicit and unrecorded.
- **`python-slugify[unidecode]` is a BLOCKER, not a FLAG.** That optional extra pulls `Unidecode`,
  which is GPL-only. If §3 adopts `python-slugify` (R5), the plain package is fine and the
  `[unidecode]` extra must never be installed. Write that down next to the dependency.
- `rgbcolor` 1.0.1 (npm) — `MIT OR SEE LICENSE IN FEEL-FREE.md`; elect MIT.
- `dompurify` — `(MPL-2.0 OR Apache-2.0)`; elect Apache-2.0. Both arms are on the OK list.

### 2.3 No `THIRD-PARTY-NOTICES` file exists

`find . -iname "*NOTICE*" -o -iname "*THIRD*PARTY*" -o -iname "*ATTRIBUTION*"` → **zero results**
(re-verified on `origin/dev`). `docs/` has no licensing document. `.github/workflows/` has 18
workflows and **none scans licences** (`cla.yml` is a signature check).

Meanwhile taOS redistributes binary wheels that statically bundle third-party code:

- **`sqlcipher3` 0.6.2** declares `License-Expression: MIT`, which is wrong on its face. The shipped
  licence file is the **Zlib-style pysqlite licence** ("Copyright (c) 2004-2007 Gerhard Häring"),
  and the artefact is a **20.3 MB static `.so`**; `strings` on it returns `OpenSSL 3.6.0 1 Oct 2025`
  and the SQLCipher pragma set. So one wheel carries the pysqlite wrapper (Zlib) + **SQLCipher
  Community Edition (BSD-3-Clause, © Zetetic LLC)** + **OpenSSL 3.6.0 (Apache-2.0)**. All three are
  commercially usable; all three require the notice to travel with the binary; none does.
- **`lxml` 6.1.1** (BSD-3) — manylinux wheels statically bundle libxml2/libxslt (MIT).
- **`pillow` 12.3.0** (MIT-CMU) — wheels bundle libjpeg-turbo, zlib, libtiff, libwebp, freetype.
- **`numpy`** (BSD-3 AND 0BSD AND MIT AND Zlib AND CC0-1.0), **`onnxruntime`** (MIT wrapper over many
  notice-bearing components), **`orjson`** (MPL-2.0 AND (Apache-2.0 OR MIT)),
  **`certifi`** (MPL-2.0, redistributes the Mozilla CA bundle), **`pycryptodome`**,
  **`restrictedpython`** (ZPL-2.1).
- **The macOS DMG** bundles **Sparkle 2.6.0 (MIT)** — fetched by `mac/build/build.sh`, copied in at
  `mac/build/assemble_bundle.sh:100-102`, SHA-pinned at `mac/launcher/Package.swift:12-15` — and a
  full **python-build-standalone CPython 3.12.13+20260414** (build scripts MPL-2.0; the tarball
  ships its own `licenses/` dir covering CPython PSF-2.0 plus OpenSSL, libedit/ncurses, sqlite,
  bzip2, xz, zlib, libffi, tcl/tk). Neither surfaces in the DMG. *(Unverified: whether the
  `aarch64-apple-darwin` `install_only` build links libedit (BSD) or GNU readline (GPL-3.0). PBS
  policy is libedit; the licensing doc URL 404s. Unpack the tarball's `licenses/` dir before the
  next DMG ships.)*
- **Apple `container` CLI 0.12.0**, fetched by `mac/build/fetch_container_cli.sh:28` — Apache-2.0
  *(unverified — asserted from the upstream repo, not fetched)*; §4(d) NOTICE not reproduced.
- **two vendored copies of three.js r2026** (`desktop/public/gamestudio-seeds/orbit-shooter/three.module.js`
  and `.../platformer-lite/three.module.js`) — the only vendored third-party source in the tree.
  The `@license` header and `SPDX-License-Identifier: MIT` line are intact, which is the
  industry-normal minimum, but MIT strictly requires the full permission notice to travel.

**Remedy: one generated `THIRD-PARTY-NOTICES.md`** (`pip-licenses` + `license-checker` into a
template), shipped in the sdist, the DMG and the SPA. One task, closes this whole subsection.

### 2.4 The store cover images are unlicensed third-party art

`desktop/public/store-covers/*.webp` — 16 images (code-server, comfyui, hermes, home-assistant,
immich, jellyfin, n8n, nextcloud, ollama, openclaw, radarr, sonarr, stable-diffusion,
stable-diffusion-bw, uptime-kuma, vaultwarden), referenced from
`desktop/src/apps/StoreApp/index.tsx:77+`. The commit message for `65804ad59` says so verbatim:
*"Official screenshots/hero art for the real apps … saved as optimized webp"*, and `b4b2af7d5` adds
*"a grayscale cut of the same banner"* — a **derivative work**. No licence recorded for any of them,
no attribution, distributed publicly. No fair-use safe harbour was checked, and provenance was not
verified per image **(unverified)**.

This is the weakest link after tldraw and the cheapest to close: the StoreApp already falls back to
a gradient when `coverImage` is absent, so removal is a one-line change per entry.

By contrast, `static/store-icons/brands/*.svg` (30 files) are **Simple Icons**, verified
**CC0-1.0** — no copyright obligation. Trademark is a separate, unresolved question (Simple Icons
explicitly disclaims trademark rights; Meta, WhatsApp, Google, Atlassian and Docker have restrictive
brand policies). The use is nominative — identifying the app being installed — which is the
strongest defensible position, but it is not a licence.

`static/store-icons/openclaw.jpg` and `static/app-icons/generic-service.svg` have unrecorded origin
**(unverified)**. `desktop/src/apps/musicstudio/audio-engine.ts:9,18` fetches `SplendidGrandPiano`
and `Soundfont` sample sets from a CDN at runtime — the `smplr` package is MIT but the **samples are
separately licensed** and commonly CC-BY **(unverified — the exact sets smplr 1.0.0 pulls were not
resolved)**. Worth twenty minutes.

### 2.5 AGPL §13 has no surface in the SPA

`grep -rn "AGPL\|Affero" desktop/src` → **0 hits** (re-verified on `origin/dev`).
`SettingsApp/` contains Account, Logs, Notifications, Themes, Updates, Users — no About panel, no
licence text, no source offer. AGPL §13 requires network users be *prominently offered* the
Corresponding Source. The only GitHub links in the SPA are three deep links
(`ActivityApp.tsx:569`, `ClusterApp.tsx:1286`, `chat/MessageList.tsx:252`) and they point at the
**old repo name** while the README uses the new one; if the old name ever stops redirecting, the
§13 offer breaks.

**Remedy:** an About panel carrying the AGPL notice, a link to `THIRD-PARTY-NOTICES.md`, and the
source offer. Same task as §2.3.

### 2.6 The project's own licence declarations are inconsistent

| Surface | Says | Verdict |
| --- | --- | --- |
| `LICENSE` (544 lines) | AGPL-3.0 verbatim | OK |
| `README.md` licence section | AGPL-3.0-or-later + `COMMERCIAL-LICENSE.md` + contact address | OK, and well written |
| `COMMERCIAL-LICENSE.md` | AGPL public; commercial licence granted by jaylfc; three named use cases | OK, with the carve-out caveats below |
| `CLA.md` | Individual CLA v0.1 granting a relicensing right; self-described "Interim version, to be ratified by an IP lawyer" | Adequate; the clause-1 relicensing grant is the load-bearing part and it is present. No corporate variant exists. |
| **`pyproject.toml:9`** | `license = { file = "LICENSE" }` — no SPDX expression, no classifier | **Fix:** `license = "AGPL-3.0-or-later"` + `license-files = ["LICENSE"]` (PEP 639, supported by the pinned setuptools). Today the `dist-info` carries the raw AGPL text where an SBOM expects an expression. |
| **`desktop/package.json`** | no `license` field at all (`"private": true`) | **Fix:** add `"license": "AGPL-3.0-or-later"`. The SPA is the AGPL'd work users actually interact with. |
| **every source file** | no `SPDX-License-Identifier` header anywhere except the two three.js copies | **Fix:** `SPDX-License-Identifier: AGPL-3.0-or-later` + `SPDX-FileCopyrightText` headers. For a dual-licensed project these are what let a lawyer, an SBOM tool or a future contributor tell taOS-owned code from imported code. |

**What a commercial licensee would trip over:**

1. The commercial licence **cannot be granted for the whole product as it stands** — not over tldraw
   (B1), not over litellm-enterprise (B2), and not cleanly over zeroconf/pystray/python-xlib without
   passing on the LGPL relink obligations.
2. `COMMERCIAL-LICENSE.md` is **silent on third-party components**. A normal commercial licence has
   a "Third-Party Components" clause pointing at a notices file. That file does not exist (§2.3).
3. **`taosmd` is MIT, not AGPL** — verified from the installed `dist-info` ("MIT License, Copyright
   (c) 2026 jaylfc"). Legally fine as a dependency, but the memory system, the project's headline
   differentiator, can be lifted into a closed competitor for free. That contradicts the model
   applied to everything else and deserves an explicit, recorded decision.

### 2.7 Other hygiene the audit surfaced

- **`jinja2` 3.1.6 is a declared core dependency with zero imports anywhere in the repo** —
  `grep -rn "import jinja2\|from jinja2" tinyagentos/` → 0 (re-verified). Either put it to work or
  drop it. The stored XSS it was nominated for (§3.1 S1) has since been closed with stdlib
  `html.escape`, so nothing depends on that decision any more.
- **`chardet` 7.4.3 is 0BSD today but was LGPL-2.1+ through 5.2.0.** The lock pins a good version;
  a careless bump backwards re-introduces an LGPL core dependency. This is exactly the shape a
  licence-scan gate catches.
- **The 134-entry app catalog is not contamination.** Jellyfin (GPL-2.0), Immich/Nextcloud/
  Vaultwarden (AGPL-3.0), Sonarr/Radarr (GPL-3.0) and the rest are separately downloaded processes
  and containers, never linked into taOS. `app-catalog/catalog.yaml` carries only ids, names and
  descriptions. `tinyagentos/routes/setup.py:238` already implements per-app licence acceptance for
  the RK NPU stack, so the pattern exists if it is ever needed more widely.
- **Armbian images are a live obligation if prebuilt `.img` files are ever distributed.**
  `os-build/build.sh` clones the Armbian build framework (GPL-2.0) at build time and deliberately
  does not vendor it — correct, and it pins by tag *and* by immutable commit SHA to defeat tag
  retargeting, which is exemplary. But a distributed `.img` contains a whole Debian/Ubuntu userland,
  which makes taOS a GPL redistributor owing a §3 source offer. Standard practice is a written offer
  plus a pointer to Armbian/Debian source. Not present.
- **Add a licence-scan CI job** (`pip-licenses --fail-on` + `license-checker --failOn`) so the next
  tldraw-shaped relicense trips a gate instead of a hand audit — pointed at what
  `scripts/install-server.sh` installs, not only at `uv.lock`, because those differ today (B2).

### Recommended order for §1–§2

1. Replace tldraw with Excalidraw (B1). Do not cut another release with it unlicensed.
2. Get `litellm-enterprise` out of the install path (B2).
3. Delete or re-source the 16 store covers (§2.4) — one line per entry.
4. Generate `THIRD-PARTY-NOTICES.md` and ship it in the sdist, the DMG and the SPA, linked from a
   new About panel that also carries the §13 source offer. One task, closes §2.3 and §2.5.
5. Fix the declarations (§2.6) and the SPA's stale repo links.
6. Add the licence-scan CI job.
7. Decide consciously whether `taosmd` stays MIT.
8. Get `CLA.md` ratified and add a corporate variant.

---

## 3. Ranked REPLACE / WRAP list

Ranked by *(live defect severity × how cheap the fix is)*. Every row traces to a slice report;
where two slices found the same thing the row is merged and both are cited. **Effort** is S/M/L for
the migration itself, not for the follow-on testing.

### 3.1 Security-adjacent findings (do these first)

Every claim in this subsection was re-read against the actual source file on `origin/dev` before
being written down; the line numbers are current.

| # | Area | Current code | Concrete defect | Library (SPDX) | Effort | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **S1** | Stored XSS, notifications | `tinyagentos/routes/notifications.py:50-51` (~12 LOC fragment builder) | `f'<div class="notif-title">{level_icon} {item["title"]}</div>'` and the sibling `notif-meta` line interpolated **unescaped** (pre-fix). `tinyagentos/routes/broker.py:64-70` builds `message` from `body.agent_identity`, `body.provider_id` and `body.reason` straight off the `POST /api/broker/request` body and calls `notifications.add(...)` — free-form attacker-controlled text. A `reason` of `<img src=x onerror=…>` executes in the dashboard origin, and the CSRF cookie is deliberately non-HttpOnly (`middleware/csrf.py`) so it is directly readable. `routes/project_invites.py:1365` does it right with `html.escape` — the codebase knows the rule; this path missed it. | `jinja2` 3.1.6 + `markupsafe` 3.0.3 (**BSD-3-Clause**) — **already declared core deps with zero imports anywhere** (§2.7). Minimum viable: `markupsafe.escape()` / stdlib `html.escape` at the two interpolations. Zero new weight. | **S** | **FIXED (tsk-wpjxqn)** — both interpolations now go through stdlib `html.escape`; the JSON view still returns raw text. An autoescaped template remains the nicer shape if the fragment ever grows. |
| **S2** | HTML injection, App Studio preview | `tinyagentos/routes/coding.py:555-563` (six regexes) + `:590`, `:610`, `:642-648` (~110 LOC) | Line 590 builds `f"<style>{text}</style>"` from raw `.css` bytes and line 610 builds `f"<script{remaining_attrs}>{text}</script>"` from raw `.js` bytes — **neither escapes the terminator**, so a JS string containing `</script>` (very likely in LLM-authored code) ends the block and the remainder is reparsed as HTML. Also: `_SCRIPT_TAG_RE`'s `[^>]*` (`:559`) stops at the first `>` even inside a quoted attribute; `_ATTR_RE` (`:562`) requires quotes so unquoted `src=` is never rewritten; `_CSS_URL_RE` (`:563`) truncates a data-URI `url()` at the first `)`. | `lxml.html` — **already a core dependency** (`lxml>=5.0.0`, BSD-3, ARM64 manylinux wheels). `routes/desktop_browser/rewriter.py` already does this class of job correctly with it. Zero new deps. | **M** | **REPLACE** |
| **S3** | Isolation leak, browser proxy | `tinyagentos/routes/desktop_browser/rewriter.py:45,230`; `proxy.py:399` (~35 LOC) | `_CSS_URL_RE` matches only `url()`. **`@import "x.css";` is not matched at all** — `grep -rn "@import" tinyagentos/routes/desktop_browser/` returns **zero hits** (verified), so a proxied page's `@import` fetches straight from origin, bypassing cookie isolation and the SSRF choke point. Separately `proxy.py:399` rewrites **only** `text/html`; a `text/css` response falls through to byte pass-through, so external stylesheets keep origin-absolute URLs entirely. `themes/schema.py:44` already knows `@import` exists — the knowledge is in the tree, just not here. | `tinycss2` 1.5.1 (**BSD-3-Clause**; PyPI classifier says generic "BSD License", the CourtBouillon/Kozea project ships a 3-clause LICENSE). Pure `py3-none-any`, one dep `webencodings` (BSD, pure), ~150 KB combined. The tokenizer behind WeasyPrint and bleach's CSS sanitiser. | **M** | **REPLACE** + rewrite `text/css` |
| **S4** | Theme token validation | `tinyagentos/themes/schema.py:44` `_FORBIDDEN`, `:46-51` `_check_value` | A **blocklist** guarding a value destined for a CSS declaration: `(url\s*\(\|expression\s*\(\|javascript:\|</\|<script\|@import\|;\s*}\|\\)`. `;\s*}` catches `red;}` but `red} body{position:fixed;inset:0` has no `;` before `}`, no `url(`, no `@import` → **passes**. That is a rule breakout giving arbitrary CSS on the shell (overlay / UI-redress). **Honest caveat, verified:** the only consumer today is `desktop/src/stores/theme-store.ts:468` → `root.style.setProperty(k, v)`, and CSSOM rejects an unbalanced `}`, so **no live exploit is demonstrated**. The defect is that a provably incomplete blocklist is the only server-side gate; the day anything inlines tokens into a `<style>` string it becomes live. Blocking `url(` also rejects legitimate gradients and `image-set()`. | `tinycss2` (same dep as S3): `parse_component_value_list` plus a per-token-class **allowlist** — colour tokens accept only `<hash-token>`/`<ident>`/`rgb()`-shaped functions, lengths only `<dimension>`. `{`/`}` rejected by construction. | **S** | **REPLACE** the blocklist with an allowlist |
| **S5** | Rate-limiter memory exhaustion | `tinyagentos/rate_limit.py:67` (`self._buckets: dict[str, TokenBucket] = {}`); `tinyagentos/auth_middleware.py:445` (`_rate_limit_hits: dict[str, tuple[float, int]] = {}`), `:458-463`; plus `routes/peer.py:44-80` and `routes/desktop_browser/push.py:73` — **four independent limiters**, and `auth_middleware.py`'s own docstring names a fifth in `cluster.py` it says it "mirrors … exactly" | *(a)* Neither of the first two dicts is ever pruned — no `del`, no eviction, no maximum size anywhere in either file (verified). The key is the client IP; an attacker with an IPv6 /64 has 2^64 distinct keys, each costing a dict entry. On a 4 GB Pi that is a reachable OOM **through the public, unauthenticated project-invite-redeem endpoint**. `routes/peer.py` already has the fix (`_RATE_HITS_MAX_SIZE = 2000` plus opportunistic sweep) — it just never propagated. *(b)* Fixed-window burst doubling: 20 requests at t=9.9 s plus 20 at t=10.1 s is 40 in 0.2 s against a limiter documented as "20 per 10 s" — on an endpoint whose proof-of-possession is an 8-digit numeric PIN, that halves the brute-force cost. *(c)* `auth_middleware.py:458` uses `time.time()`, so an NTP step backwards (routine on an RTC-less Pi) freezes the window; the token bucket correctly uses `time.monotonic()`. *(d)* No `Retry-After` on any 429. | `limits` 5.8.0 (**MIT**, verified). Pure `py3-none-any`; deps `deprecated`, `packaging`, `typing-extensions` — all pure, and `packaging` is already resolved (R2). `MovingWindow` + `MemoryStorage` fixes (a) and (b) together and `get_window_stats` gives the `Retry-After` value. `slowapi` 0.1.10 (MIT) layers the FastAPI decorator on top but is much smaller and less active — take `limits` alone. | **M** | **DONE** — one shared limiter, dependency declined. `tinyagentos/rate_limit.py` now holds a bounded `MovingWindowLimiter` (moving window, `time.monotonic()`, LRU eviction at `MAX_TRACKED_KEYS`), a bounded `RateLimiter`, and one `rate_limited_response` helper that always sets `Retry-After`; all five limiters use them. `limits` was not added: the whole mechanism is about 120 lines of pure stdlib and taOS installs offline on 4 GB ARM boards. `push.py`'s per-user limiter is unchanged (keyed on user id, so the key space is bounded by the account list). |
| **S6** | CSRF token has no binding | `tinyagentos/middleware/csrf.py:93` (`secrets.token_hex`), `:152-159` (compare) — 112 LOC, otherwise a correct and well-documented double-submit | The token is random and **unbound**: minted at `:93`, compared only against itself at `:158` (`secrets.compare_digest(cookie_token, header_token)`). Plain double-submit assumes nobody can write cookies onto the victim's origin. Under the `{user}.taos.my` subdomain model, anything that can set a `Domain=.taos.my` cookie can plant a `csrf_token` it also knows and submit a matching header; the browser sends both host-only and domain cookies under one name and `conn.cookies.get(...)` at `:152` resolves non-deterministically. Secondary: no `secure=True` — defensible on a plain-HTTP LAN, but the cookie is also plantable by an active LAN attacker. | **Stdlib `hmac`** over the session id is the recommendation. `starlette-csrf` 3.0.0 (**MIT**) implements the signed variant but was last released 2023-06-27 — stale, and it adds `itsdangerous` for a property four stdlib lines give. | **S** | **WRAP** — bind the token with `hmac(secret, session_id)` |
| **S7** | Archive bombs | `tinyagentos/themes/package.py:33,49` — **no member-count, no per-member and no total-size limit at all**; `tinyagentos/routes/settings.py:307-332` (`restore_backup`) — no cumulative size cap | `themes/package.py` decompresses declared sizes straight into memory via `zf.read(...)` at both `:33` and `:49`, and `routes/themes.py:29` does `await package.read()` with no upload cap — **a 40 KB `.taostheme` can exhaust a 4 GB Pi**. Path traversal *is* handled there (`:37-46`); only the bomb case is missing. `restore_backup` checks `..` and absolute paths but has no cumulative cap, so a gzip bomb writes unbounded into `data_dir`. The correct implementation already exists in-tree: `tinyagentos/userspace/package.py:25-27` defines `_MAX_UNCOMPRESSED_BYTES`/`_MAX_MEMBER_BYTES`/`_MAX_MEMBERS` and `:132-141` checks them over `infolist()` **before any read** (verified). | **Stdlib is the library.** `tarfile.extractall(..., filter="data")` (PEP 706) plus one shared zip helper modelled on `userspace/package.py`. `desktop_rebuild.py:169` already does the tar half correctly, including an explicit refusal on Pythons lacking the filter. No third-party "safe extract" package is worth a dependency. | **S** | **KEEP custom, CONSOLIDATE** into `tinyagentos/safe_archive.py`. Give `themes/package.py` limits now. |
| **S8** | `window.eval()` slips the detector | `tinyagentos/code_analyzer.py:135` — `re.compile(r"(?<!\.)\beval\s*\(")`, one of nine line-anchored detectors gating App Studio publish/install | The negative lookbehind avoids false-positiving a method named `eval`, but it also means **`window.eval("…")`, `globalThis.eval("…")` and `self.eval("…")` are not flagged** — ordinary indirect eval, trivially reachable from LLM output. The module's docstring lists two other blind spots and **not this one**, so it reads as covered. Related: `_DANGEROUS_SCHEME_RE` requires a quote, so an unquoted `href=javascript:…` is missed; `_iter_lines()` makes every proximity heuristic degenerate on a minified single-line bundle. The same lookbehind shape appears at `:348-350` for `top.location` / `parent.postMessage` / `opener.*`. | `tree-sitter` 0.26.0 + `tree-sitter-javascript` 0.25.0 (**MIT** both, verified). aarch64 manylinux/abi3 wheels published — 632 KB + 106 KB, no compile at install and **no Node subprocess**, which answers the module docstring's stated objection directly. Error-tolerant by design, which matters when the input is LLM output. | **S** (lookbehind) / **L** (tree-sitter) | **KEEP the regex layer, fix the lookbehind now**; treat tree-sitter as an *additional* pass, never a replacement — a false-negative regression in a security gate is worse than a known gap. **Done (tsk-lpdd2e):** the indirect-eval forms, the unquoted `href=javascript:` shape and the same lookbehind at `:348-350` are now flagged, and the module docstring's blind-spot list and its (stale) Node-subprocess objection were corrected. The tree-sitter pass is still open. |
| **S9** | Three divergent SSRF guards | `tinyagentos/routes/desktop_browser/ssrf.py` (176 LOC, richest); `tinyagentos/userspace/url_guard.py` (66 LOC, the only one that **pins** the validated IP); `tinyagentos/projects/canvas/unfurl.py:27-37` (~11 LOC, weakest) | `unfurl.py` checks `ipaddress` flags only and **does not pin**: it resolves at `:29`, then hands the *hostname* to httpx at `:56`, which resolves again — DNS-rebinding TOCTOU. It also sets `follow_redirects=False` as a redirect-bypass defence, with the consequence that any 301/302 (essentially every bare `http://` link and most shorteners) falls to `_fallback(url)` at `:149` and the user gets a card titled with the raw URL — a security choice that silently degrades the feature instead of walking the chain with re-validation, which `desktop_browser/ssrf.py`'s own docstring says callers must do. `url_guard.py` lacks the suffix blocklist, encoded-IPv4 parsing and CGNAT backstop that `ssrf.py` has. | **None.** No maintained Python SSRF guard is worth adopting (`advocate` is unmaintained and pins old `requests` **(unverified)**). For the metadata half of the unfurler, `lxml.html` (already core) beats the `HTMLParser` subclass at `unfurl.py:68-96`, where `"icon" in rel` also matches `apple-touch-icon`/`mask-icon` so the favicon can be the wrong asset. | **M** | **KEEP custom, CONSOLIDATE** — promote `desktop_browser/ssrf.py` to `tinyagentos/ssrf.py`, fold in `url_guard`'s IP pinning, delete the other two, give the unfurler a bounded re-validating redirect walk |
| **S10** | Local code execution, rollback | `scripts/rollback.sh:32-37`, escalates with `sudo` at `:97` | `source .taos-rollback` **executes a data file as bash**. That file lives in `$INSTALL_DIR`, which `set_data_dir_ownership` deliberately `chown -R taos:taos`. A compromised agent container with a bind mount, an updater bug, or a partial write after a power cut becomes arbitrary code execution under sudo. A truncated file also yields an empty `prev_sha` and falls to the "cannot resolve" path instead of the recovery-tag fallback at `:39`. | **None needed** — JSON plus the venv python, or `sed -n 's/^prev_sha=\([0-9a-f]\{7,40\}\)$/\1/p'` with charset validation. | **S** | **REPLACE the parse** |
| **S11** | Graceful shutdown suppressible by any local user | `scripts/taos-graceful-stop.sh:18-29` | When `/run` is unwritable the stamp falls back to a world-writable temp directory; the sticky bit does not prevent *creating* a file. Lines 24-28 check only `-f` plus mtime < 60 s, so a cron-refreshed squat **permanently suppresses the prepare-shutdown call** and agents never drain on restart or reboot. Given this hardware's `data=writeback` corruption history, silently skipping the drain is the wrong failure. Secondary: `stat -c %Y` is GNU-only. | **None** — `RuntimeDirectory=taos` + `RuntimeDirectoryMode=0750` in the unit; for the no-systemd path use `$INSTALL_DIR/data/` (already 0700). | **S** | **REPLACE the stamp location** — **done** (tsk-gkupkv): stamp moved to `RuntimeDirectory=taos` / `$INSTALL_DIR/data`, age read from an epoch written into the file, regression at `tests/scripts/test_graceful_stop_stamp.py` |
| **S12** | `dompurify` is a phantom dependency | `desktop/package.json:94` — declared **only under `overrides`**, never under `dependencies` (verified); imported directly at `desktop/src/apps/BrowserApp/ReaderMode.tsx:8` and used at `:34` to sanitise Readability HTML before `dangerouslySetInnerHTML` at `:139` | `overrides` does not install a package — it only constrains the version of one a transitive dep already pulls (here `mermaid`). The direct import resolves by accident of hoisting. If mermaid ever drops or renames its DOMPurify dep the build breaks; under a different resolver layout it can resolve to a *different* hoisted copy than the one the override pins, quietly reverting the `^3.4.0` security pin. This is the sanitiser between a hostile web page and `dangerouslySetInnerHTML`. | `dompurify` itself — dual **Apache-2.0 OR MPL-2.0**, both on the OK list. Already in tree; **zero added weight**. Fix is one line: promote to `dependencies` at `^3.4.0`. The other overrides-only specifiers (`lodash-es`, `uuid`, `nanoid`, `esbuild`) were checked — none is imported directly by `desktop/src`, so only `dompurify` is exposed. | **S** | **REPLACE the phantom with an explicit dependency** |


### 3.2 Ranked REPLACE / WRAP list

Line numbers in this table are as recorded by the slice auditor on `exec/tsk-itxxdo` unless a row
says otherwise. Rows marked **proven by running** were demonstrated by executing the real function
in the project venv, not by reading it.

| # | Area | Current code | Concrete defect | Library (SPDX, maturity, ARM64, weight) | Effort | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **R1** | Byte/size parsing — **five** Python parsers, **eight** JS formatters | Py: `containers/backend.py:23-37` `_parse_memory`; `cluster/worker_capacity.py:21-28`; `routes/agent_images.py:104-130` (the correct one); `cli/worker.py:107-116`; `disk_quota.py:21,183`. ~75 LOC. JS: `apps/FilesApp.tsx:103-109`, `components/LibraryItemCard.tsx:58-63`, `apps/ActivityApp.tsx:163-173`, `apps/GitHubApp.tsx:72-76`, `apps/LibraryApp.tsx:173-177`, `apps/LoRAStudioApp.tsx:34-38`, `components/ModelBrowser.tsx:84-87`, `apps/ModelsApp.tsx:301` | **Two live bugs on values taOS itself writes.** `userspace/container_deploy.py:18` sets `_MEMORY_LIMIT = "512m"` (verified) → written via `containers/__init__.py:298` → read back through `_parse_memory("512m")`, upper-cased to `"512M"`, matches no `GB/MB/KB` suffix, falls to `int("512M")` → `ValueError` → **`return 0`**. Every userspace app container reports `memory_mb=0`. `cluster/worker_capacity.py:26` *raises* on the same input — the two fail in opposite directions. `tests/test_containers.py:14-23` covers only `"2GB"/"512MB"/"0"/""`, exactly the four inputs production never produces. `disk_quota.py` has **no TiB unit** in either parser, so a container over 1 TiB produces no match, no record, no notification and the quota is **never enforced**, indistinguishable from "no disk usage". JS side: `FilesApp.tsx:106` has no clamp on a 5-entry unit array, so a 2 PB volume renders `"1.8 undefined"` and a negative size renders `"NaN undefined"`; `LibraryItemCard.tsx:61` gets it right, proving the divergence is accidental. [domain F1 + core F10 + desktop F7] | Py: `humanfriendly` 10.0 (**MIT**, verified). Pure `py2.py3-none-any`, ~90 KB, **zero transitive deps on Linux/py3**. Caveat: **last release 2021-09-17** — mature and frozen, but effectively unmaintained; for one `parse_size` call a stdlib unit table in a shared `tinyagentos/size_units.py` is the better trade. JS: **`Intl.NumberFormat`** — native, **0 bytes**, no clamping bug possible. `pretty-bytes` (MIT, zero deps, ~1 kB) only if strict binary prefixes are wanted. | **S** | **WRAP** — one `size_units.py` for Python (or consolidate onto `routes/agent_images.py::_parse_size_bytes`, already correct), one clamped helper for JS. Best value-to-effort in the audit. |
| **R2** | Version comparison | `worker/update_check.py:135-207` (~70 LOC); `routes/apps.py:83-98` `_semver_tuple`, used at `:275` for `update_available` | **Proven by running.** `update_check.py:140` is `v = v.split("-")[0].split("+")[0]` (verified on `origin/dev`), which throws away the entire pre-release segment. `pyproject.toml` declares `1.0.0-beta.50`. In the project venv: `_parse_version('1.0.0-beta.50') -> (1,0,0)`; `_parse_version('1.0.0-beta.51') -> (1,0,0)`; `is_newer_version('1.0.0-beta.51','1.0.0-beta.50') -> False`; `is_newer_version('1.0.0','1.0.0-beta.50') -> False`. **A worker on the beta channel can never update, and no worker can cross from beta to GA.** `version_matches_pin` inherits it. `_channel_from_version` is substring matching, so any version string containing "dev" is classified dev-channel. `routes/apps.py:94` additionally returns `(0,0,0)` on parse failure, so an unparseable recorded version reads as older than everything → permanent spurious "update available". [core F1 + domain F2] | `packaging` 26.3 (**Apache-2.0 OR BSD-2-Clause**, verified from the venv `License-Expression` field). PyPA-maintained, the PEP 440 reference implementation, pure `py3-none-any`, **zero runtime deps**, ~100 KB. Already resolved in `uv.lock` at 26.2 — but only via the `proxy` extra and the dev group, so a bare `uv sync` does not get it; declare it. Verified: `parse('1.0.0-beta.51') > parse('1.0.0-beta.50') -> True`. `Version.is_prerelease`/`.pre` replace the channel guessing exactly. | **S** | **REPLACE.** Watch the known release-train gotcha — the normalised form is `1.0.0b50`, so any raw-string comparison must change in the same PR. |
| **R3** | Logging is never configured | Absence, not presence. `dictConfig` appears nowhere; `basicConfig` only in four standalone script entry points (`disk_quota.py:320`, `services/sdcpp_server.py:37`, `worker/browser_main.py:105`, `benchmark/runner.py:461`). `app.py` and `__main__.py` rely on uvicorn's defaults, while every module does `logging.getLogger(__name__)` — several hundred `logger.info` call sites | **Proven by running.** uvicorn's `LOGGING_CONFIG` defines loggers for `uvicorn`, `uvicorn.error` and `uvicorn.access` only — **no `root` entry**. Reproduced in the project venv: `handlers on root: []`, effective level `30`, `log.info(...)` emits nothing, `log.warning(...)` emits a bare sentence via `logging.lastResort`. So **the entire backend's INFO logging is silently discarded in production**, and every WARNING/ERROR arrives with no timestamp, no level prefix and no logger name. `db_migrations`' "applying migration v%d", `auth.py`'s session-prune line, `restart_orchestrator`'s progress — never written anywhere. This is the invisible half of every "why did X not happen" investigation and it silently weakens several other rows here (R15's only symptom is a warning line). | **Stdlib first:** one `logging.config.dictConfig({...})` in `create_app()`/`main()` adding a `root` logger with uvicorn's `default` handler and `%(asctime)s %(levelname)s %(name)s: %(message)s`. Optional follow-on: `structlog` 26.1.0 (**MIT OR Apache-2.0**, verified), pure `py3-none-any`, **no runtime deps** on py>=3.11, giving JSON-per-line output the Logs app and `log_redaction.redact()` could consume structurally instead of by regex over free text. | **S** | **REPLACE** with a real logging config. Pair it with a `TAOS_LOG_LEVEL` knob — volume jumps from near-zero to full INFO. |
| **R4** | Retry helper misses the commonest transient error | `clients/retry.py:1-145` (~60 LOC of retry logic in three near-duplicate `except` arms). Wraps every outbound inference call and every framework adapter's `/message` proxy — 14 call sites | **Proven by running.** *(a)* `DEFAULT_RETRY_ON` (`:25-29`, verified on `origin/dev`) is `(ConnectError, ReadTimeout, RemoteProtocolError)`. In httpx's hierarchy `ConnectTimeout` inherits `TimeoutException -> TransportError`, **not** `ConnectError` — `issubclass(httpx.ConnectTimeout, httpx.ConnectError) -> False`, and with `max_attempts=4` against a coroutine always raising `ConnectTimeout`: **attempts made: 1**. A connect timeout is exactly what a lazily-started local backend produces while booting (`llm_proxy.py:507` waits up to 120 s for LiteLLM), so the single most common transient failure is the one case not covered. `PoolTimeout` and `ReadError` likewise. *(b)* On the final attempt the `except _StatusError` arm (`:96-107`) neither re-raises nor breaks, so control falls to `raise last_exc` and a **module-private exception type escapes to callers** — verified: `503 exhausted -> tinyagentos.clients.retry._StatusError`. Every upstream `except httpx.HTTPStatusError` misses it and it surfaces as an unhandled 500. *(c)* No overall deadline: adapters call it with `max_attempts=7, max_delay=60.0` around a `timeout=60` POST — worst case roughly 450 s inside one handler, while `channel_hub/router.py:50` gave up at 120 s. *(d)* 429 is not retried and `Retry-After` is honoured nowhere. | `tenacity` 9.1.4 (**Apache-2.0**, verified on PyPI). Pure `py3-none-any`, **no runtime dependencies**, the standard Python retry library with first-class `AsyncRetrying`. The missing primitives map one-to-one: `retry_if_exception_type(httpx.TransportError)`, `stop_after_delay(...)`, `wait_exponential_jitter`, `retry_if_result`. It re-raises the *original* exception, which fixes (b) by construction. `stamina` (MIT) is a thin wrapper over the same thing — a second package for no extra capability. | **S–M** | **WRAP** — keep `with_retry()` as the public API, implement with `tenacity`. Widen to `httpx.TransportError`, add 429 plus `Retry-After`, add a `max_total_seconds` deadline below the router's 120 s. |
| **R5** | Slugify rejects every non-Latin name | `config.py:250-264` `slugify_agent_name` (gated by `validate_agent_name` at `:233-248`); near-identical copy at `agent_registry_store.py:404-407`; JS copy at `desktop/src/lib/slug.ts:1-9` plus a divergent inline copy at `components/ConsentActions.tsx:30-32` | **Proven by running.** The character class is ASCII-only, so every non-ASCII code point is deleted *before* the emptiness check: a CJK name and a Cyrillic name both slug to the empty string and `validate_agent_name` then answers **"Agent name must contain at least one letter or number"** for a name that is entirely letters. **A user cannot name an agent in Chinese, Japanese, Korean, Cyrillic, Greek, Arabic, Hebrew or Thai at all.** Accented Latin is accepted but silently loses the accent, so two differently-spelled names collide. Worse, `agent_registry_store._slugify`'s `or "agent"` fallback means two agents with non-ASCII names registered in the same second both mint the same canonical id — an **identity collision in the very table that exists to hold immutable agent identities**. On the JS side `lib/slug.ts` has **no** fallback, so `DeployWizard.tsx:1009` shows an empty derived slug with no explanation. Minor: `unique_agent_slug` (`config.py:267`) appends numeric suffixes to an already-63-character slug, overrunning the limit the truncation exists to respect. [core F4 + desktop F14] | `python-slugify` 8.0.4 (**MIT**, verified). Pure, 10 KB, already resolved in `uv.lock` (today only in the `e2e` extra). Verified output: the CJK and Cyrillic inputs both transliterate to distinct non-empty slugs and accents round-trip. **Licence note (see §2.2):** it depends on `text-unidecode` (Artistic-1.0 OR GPL-2.0+) — usable by electing the Artistic arm, which must be recorded; and `python-slugify[unidecode]` must **never** be installed. Zero-dep alternative: `unicodedata.normalize("NFKD", …).encode("ascii","ignore")` plus a deterministic `blake2s` hash fallback — fixes Latin and removes the collision, but does not transliterate CJK. JS: `@sindresorhus/slugify` (MIT, ~3 kB) or NFKD plus a non-empty fallback. | **S** | **REPLACE** (recording the Artistic-arm election) or **WRAP** with NFKD plus a hash. Either way `_slugify`'s `or "agent"` collision path has to go, and the two JS copies must collapse into one. |
| **R6** | Migration runner: no async busy_timeout, no per-migration transaction | `db_migrations.py` (395 LOC) — `apply_wal_pragmas` `:93`, `apply_wal_pragmas_async` `:249`, `run_migrations` `:159`, `run_migrations_async` `:311`. `base_store.py:43` calls the async helper for **all 78 `BaseStore` subclasses** | *(a)* The sync helper sets `busy_timeout = 5000` at `:95`; **the async helper omits it entirely** (verified on `origin/dev`: `:251` sets `journal_mode`, and `busy_timeout` appears nowhere in that function). SQLite's default is 0 ms, so any writer finding the write lock held returns `SQLITE_BUSY` immediately. WAL removes reader/writer contention, not writer/writer, and taOS has several writers per DB file (controller, `taosctl`, worker). Three stores worked around it individually — `agent_budget_store.py:49`, `litellm_keystore.py:52`, `broker/store.py:82` each re-issue the pragma after `super().init()` — which is the tell: the base helper should have done it and the other 75 never got the memo. *(b)* `:234` (and `:388` on the async path) is `conn.executescript(step)`; `sqlite3.executescript` **issues an implicit COMMIT first**, so a five-statement migration failing on statement three leaves one and two durably applied with **no version row**. The next boot re-runs from the top and dies on `duplicate column name`, bricking that store with no recovery short of manual SQL. *(c)* `:190-215`: a new namespace in a DB file that any other table already populated is stamped at `latest_version` and **every one of its migrations is skipped on every existing install**. The module docstring documents this as "FOOTGUN 2" and prescribes guarded `_post_init` coroutines instead — meaning the migration system is knowingly unusable for its stated purpose on exactly the databases that need it. | **None — do not migrate the runner.** `alembic` 1.19.1 (MIT) requires SQLAlchemy plus Mako, a large install-weight and conceptual cost on a 4 GB target for a codebase using raw `aiosqlite`. `yoyo-migrations` 9.0.0 (Apache-2.0) is much lighter but file-based, sync-only, and has no notion of several independent namespaces sharing one connection — all 78 stores would have to move their `MIGRATIONS` lists out of Python class attributes. | **S** each fix / **L** to replace | **KEEP the runner, FIX the three defects.** Add the async `busy_timeout` (one line, fixes 75 stores, lets the three ad-hoc workarounds go); wrap each migration in an explicit `BEGIN…COMMIT`/`ROLLBACK` with the version row written *inside* it; scope the "existing tables" probe to tables this namespace owns. |
| **R7** | `save_config` bypasses the repo's own crash-safe writer | `config.py:361-365` (five lines: mkdir, write to a `.yaml.tmp`, `replace`). `tinyagentos/atomic_io.py` (99 LOC) already exists and its module docstring is an incident report | The five lines reproduce **exactly the failure mode `atomic_io` was written to prevent**: on 2026-08-21 an unclean power-off left `data/.auth_user.json` as 901 NUL bytes with intact size and mtime, and the auth layer read that as "no users exist" and served first-run onboarding to an unauthenticated caller. No fsync of the file, no fsync of the parent directory. `config.yaml` holds the entire install — ports, every backend, every agent record — and `save_config` is invoked on the `_pin_applied` path at **boot** (`config.py:227`), precisely the window a first-boot power cut hits. Second defect in the same five lines: the temp path is **deterministic**, where `atomic_io.py:57` uses `secrets.token_hex(8)` specifically so two concurrent writers cannot share one temp inode and interleave bytes. Eight further hand-rolled copies, none of which fsync: `store_popularity.py:313`, `projects/beads_bridge.py:230`, `projects/canvas/snapshotter.py:211`, `taosnet/mesh_credentials.py:88`, `routes/observatory.py:75`, `github_app_installations.py:63`, `hub/identity.py:112`, `installers/rkllamacpp_installer.py:180`. `atomic_io` is imported by exactly one module in the whole tree. | **None — the in-repo module is the library**, and it is careful in ways most third-party code is not (random temp name, `O_EXCL`, partial-write loop, mode applied before the rename, directory fsync with a narrow errno allowlist for mounts that cannot fsync a directory). For the record: the PyPI `atomicwrites` package is explicitly unmaintained and deprecated by its own author, and is *weaker* — it does not fsync the parent directory. **Do not adopt it.** | **S** | **REPLACE with the in-repo library** — nine one-line swaps. Then add a CI grep banning a `.write_text(` followed by a `.replace(` on a `tmp` path outside `atomic_io.py`, so a tenth copy cannot appear. |
| **R8** | No cross-process locking anywhere | `auth.py:29-41` (`_serialized`, a `threading.RLock`), `auth.py:57-125` (`_PersistentSessions`, `threading.Lock`), `config.py:40` (`asyncio.Lock`). Searching the whole package for `fcntl`, `flock` or `LOCK_EX` returns only PTY ioctls — **there is no file lock in the codebase** | `app.py:1810-1877` registers `taos recover-password` as a top-level CLI verb that constructs `AuthManager(data_dir)` and does a read-modify-write of `.auth_user.json` **in a separate OS process while the controller is running**. A `threading.RLock` is process-local and provides zero protection. The interleaving: the CLI reads the users list; the operator invites a user in the web UI and the server reads, appends and writes; the CLI then writes its own edited copy; **the invited user is gone**, with no error on either side. The same exposure applies to `config.yaml` and `data/.auth_sessions`. `_serialized`'s own docstring states the principle exactly — "two concurrent read-modify-write cycles still lose one of the two edits … Serialising the cycle is the missing half" — and then serialises only within one process. | `filelock` 3.32.5 (**MIT**, verified from the venv `License-Expression`). Pure `py3-none-any`, **zero runtime deps**, tox-dev maintained, one of the most-downloaded packages on PyPI, and **already installed** (3.29.4) and already resolved transitively via huggingface-hub — declaring it costs nothing new. `flock`-style semantics mean the kernel releases the lock on process death, so a SIGKILL leaves no stale lock; still set a finite `timeout` so a wedged holder degrades to an error rather than a hang. | **S** | **REPLACE** the process-local locks around cross-process files, keeping the in-process locks too (cheaper for the common case). |
| **R9** | Model downloads: no timeout, no retry, no resume | `download_manager.py:264-303` `_download` (~40 LOC) plus `_download_with_fallback` `:175-262` — the path that pulls multi-gigabyte weights over a home connection | *(a)* `:271` is `httpx.AsyncClient(timeout=None, …)`, which disables connect, read, write **and** pool timeouts together. A half-open TCP connection — a Wi-Fi drop, a NAT table eviction, a CDN edge that stops sending — leaves `resp.aiter_bytes()` awaiting forever. The task stays `status="downloading"`, `list_active()` keeps returning it, and **nothing ever errors**: a progress bar frozen at 63 % indefinitely, indistinguishable from a slow link. *(b)* `:287` opens the destination `"wb"` (truncating) and there is no `Range` header anywhere in the module — a 40 GB model failing at 39 GB restarts from zero. *(c)* The `except` arm at `:299-303` sets `status="error"` but, unlike the validation arm at `:295`, **does not delete the partial file**, so a later "is this model present?" check sees a corrupt weight file sitting at the canonical path. *(d)* No retry — one transient 502 from a mirror kills the transfer. *(e)* `self._tasks` is never pruned, so every download ever started stays resident. | Transport is already `httpx`, which is right. Missing pieces: `tenacity` (the same dep as R4) for retry; **no library needed** for timeouts — `httpx.Timeout(connect=10, read=60, write=60, pool=10)`, where the **read** timeout is what turns (a) from a silent hang into an error without capping total duration; **no library worth adding** for resume — a `Range` request, append mode, a check that the server actually answered `206`, about fifteen lines, and the existing SHA-256 verification catches a mis-resumed file. `huggingface_hub` 1.30.0 (Apache-2.0) gives all of it but its `requires_dist` is 112 entries wide — far too much for the 4 GB core. | **S–M** | **WRAP.** Fix (c) unconditionally — it is a one-line `unlink` in the `except` arm. Expect early reports reading as "downloads got worse" when previously invisible stalls start surfacing as errors; that is the intent. |
| **R10** | MCP supervisor never drains stdout | `mcp/supervisor.py` (217 LOC), `mcp/proxy.py` (45 LOC) | `supervisor.py:50-53` spawns with `stdout=PIPE, stderr=PIPE`; `grep -n stdout supervisor.py` returns **exactly one hit, line 52** (verified on `origin/dev`). Only stderr gets a drain task. Any server writing more than a pipe buffer to stdout **blocks forever in `write()` while `mark_running()` and `get_status()` report it healthy** — and for a stdio-transport MCP server, stdout *is* the JSON-RPC channel. Separately `proxy.py:32-43` logs "not yet wired" and returns a fake `{"ok": True, "result": "stub …"}` indistinguishable from a real result. | `mcp` (official SDK) 2.1.1 (**MIT**, verified), pure `py3-none-any`. `mcp.client.stdio.stdio_client` handles framing, handshake, capability negotiation, response correlation and **drains both pipes**. **Weight caveat — the heaviest recommendation in this audit:** 19 `requires_dist` including `httpx2>=2.5.0` (a *separate distribution* from the pinned `httpx>=0.27`, so both get installed), `jsonschema`, `pydantic>=2.12`, `pyjwt[crypto]` and `opentelemetry-api`. | **M** | **REPLACE**, or gate behind an `mcp` extra if the weight is unacceptable for the 4 GB core — but **drain stdout today either way**: a three-line `_drain_stdout` mirroring `_drain_stderr` prevents a hard hang. It replaces framing code that does not exist yet, so this is the cheapest moment to adopt it. |
| **R11** | Every OTel parent/child span link dangles | `otel/emitter.py` (374) + `receiver.py` (215) + `span_store.py` (225) + `trace_context.py` (57), roughly 870 LOC of hand-written OTLP/HTTP+JSON | Verified on `origin/dev`: `emitter.py:49-51` `_make_span_id()` returns `secrets.token_bytes(8).hex()` — **random** — and is called at `:104`; `emitter.py:112-114` derives `parent_span_id = hashlib.sha256(env["parent_id"].encode()).digest()[:8].hex()`. The parent envelope's own id was also random, never `sha256(its id)`, so **`parentSpanId` can never match any emitted `spanId`**. Every child span is an orphan in Jaeger, Tempo or Grafana and the nesting contract in `trace_context.py:1-23` silently does not hold. The `traceId` half *is* deterministic, which is why this presents as "flat trace" rather than "missing data" — the working half masking the broken half. Secondary: `_ns_from_envelope(use_end=True)` yields zero-duration spans when `duration_ms` is absent. | `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http` 1.44.0 (**Apache-2.0** both, verified), the CNCF reference implementation. The SDK is pure `py3-none-any` with four pure deps; the HTTP exporter additionally pulls `opentelemetry-proto`, `googleapis-common-protos` and `requests` (protobuf publishes aarch64 wheels). Middle path: adopt only the SDK's context and id management. | **S** (id fix) / **L** (full SDK) | **WRAP — and fix the id derivation immediately regardless.** Two lines makes roughly 870 LOC of tracing usable. |
| **R12** | Event-bus queues are unbounded and leak | `events/bus.py:50-73` plus `bridge_session.py:200-247` (~40 LOC) | `bus.py:56` is `asyncio.Queue()` with **no `maxsize`**, and `_publish_to_channel` (`:72`) does `q.put_nowait(event)` for every subscriber. An SSE client that stops draining — a suspended laptop, a locked phone, a proxy that started buffering — accumulates every system event in RAM with no bound and no drop policy. `unsubscribe` runs only if the consuming generator's cleanup executes; a task cancelled without cleanup leaves the queue registered forever and every subsequent event is appended to a queue nobody will ever read. On a 4 GB Pi that is a slow OOM with no diagnostic — and per R3 the operator will not see a log line about it either. | `sse-starlette` 3.4.10 (**BSD-3-Clause**, verified). Pure `py3-none-any`, deps `starlette` and `anyio` — **both already installed**, so marginal weight is roughly 30 KB. It handles the framing (`bridge_session.py:245` hand-builds the frame with no escaping of `event_type` and no `id:` field, so `Last-Event-ID` replay is impossible), keep-alive pings, the `Cache-Control` and `X-Accel-Buffering` headers that stop a reverse proxy buffering an SSE stream, and — most importantly — **client-disconnect detection**, which is what actually causes an abandoned subscriber to be unsubscribed. *(Verify its `requires_dist` first: one auditor saw `sqlalchemy[asyncio]`/`aiosqlite` listed, apparently extras-gated.)* It does **not** fix the unbounded queue. | **M** | **WRAP** — bound the queues now (a one-line `maxsize` plus a drop-oldest-or-disconnect policy: small, high value); adopt `sse-starlette` for the plumbing as a separate incremental change across the 14 SSE producers. |
| **R13** | Discord polled every 2 s, 429 swallowed | `channel_hub/discord_connector.py:38-76` (~40 LOC); `channel_hub/slack_connector.py:38-65`. Compare `telegram_connector.py:34-40`, which correctly long-polls | `_poll_loop` hits the channel-messages endpoint for **every** configured channel then sleeps 2 s — 30 requests per minute per channel, forever, whether or not anything happened. `_check_channel:57` is `if resp.status_code != 200: return`, so **a 429 is indistinguishable from an empty result**: the code returns, sleeps 2 s and hits the same endpoint again, **ignoring the `Retry-After` header Discord sends**. That escalates a soft rate limit into a sustained one and, on repeat, into a Cloudflare ban of the bot token. Discord's API expects the Gateway WebSocket for message events, not REST polling. Slack has the same shape at 3 s and additionally advances its cursor at `:65` **before** dispatching the messages, so if `route_message` raises, those messages are dropped and never re-fetched — at-most-once delivery on what is meant to be a message bus. | `slack-sdk` 3.44.1 (**MIT**, verified). Pure `py3-none-any` with **no required runtime dependencies**; its `SocketModeClient` replaces the 3 s poll with a push connection over the `websockets` package taOS **already depends on** — close to free. `discord.py` 2.7.1 (**MIT**, verified), pure wheel but deps `aiohttp<4,>=3.7.4` (manylinux aarch64 wheels published, so no compile at install) — a second HTTP stack, roughly 2 MB alongside httpx on a 4 GB host: a real but defensible trade. `store_popularity.py` is the in-repo model for correct remote rate-limit handling. | **M** per connector | Slack: **REPLACE** with Socket Mode. Discord: **WRAP now, REPLACE later** — honour `Retry-After` on 429 today (about five lines), move to the Gateway when the aiohttp weight is acceptable. **Fix the Slack cursor-before-dispatch ordering regardless of the library question.** |
| **R14** | Remote catalog manifests parsed with zero type validation | `registry.py:60-81` `AppManifest.from_dict` (~22 LOC of `data.get(key, default)`), fed by `from_file:55` via `yaml.safe_load`. The catalog is a **remote git repository** cloned and pulled by `catalog_sync.py:17-36`, so these are externally authored inputs | Nothing checks a type anywhere. `requires=data.get("requires", {})` happily accepts a manifest whose `requires:` is a YAML string or list; the resulting `str` travels into the install path and raises `AttributeError: 'str' object has no attribute 'get'` somewhere deep, at install time, with no indication which manifest was malformed. `context_window=data.get("context_window", 0)` accepts a string and that value flows into model-selection arithmetic. `id=data["id"]` raises a bare `KeyError` — and `registry.py:168` builds the catalog inside a loop, so **one malformed manifest in the remote catalog takes down the entire store listing**, not just its own entry. `hardware_tiers` is consumed with `isinstance` guards bolted on at each use site instead of once at the boundary. | **`pydantic` — already a dependency.** FastAPI requires it; `pydantic` and `pydantic-core` are installed and already ship the ARM64 wheel. A `BaseModel` gives per-field coercion and a `ValidationError` naming the offending field and manifest, at the boundary, for **zero new install weight**; add `ConfigDict(extra="ignore")` so newer catalog fields stay forward-compatible. `jsonschema` 4.26.0 (MIT) is the alternative if the schema should be a publishable `.json` file contributors validate against — but it pulls `attrs`, `referencing` and **`rpds-py`, a Rust extension** (aarch64 wheels published, so no compile at install, but not pure Python). Since pydantic is present, generate the JSON Schema from the model via `model_json_schema()` and get the contributor-CI benefit for free. | **S** | **REPLACE** `AppManifest` with a pydantic model, and wrap the load loop so one bad manifest is skipped with a named error instead of aborting the catalog. Run it warn-only across the live catalog once before enforcing. |
| **R15** | mDNS fails on a host with no default route | `services/mdns_publisher.py:37-51` `_detect_primary_ipv4` (14 LOC) | The function opens a UDP socket and `connect()`s to a well-known public DNS address to read back the kernel's chosen source address (verified at `:46`). Its docstring asserts this "works fine on air-gapped networks (the kernel still resolves the source interface)" — true only when a **default route exists**. On a LAN with no default route — a genuinely offline home server, a lab VLAN, a Pi on a switch with no gateway, all normal for an **offline-first** product — `connect()` raises `OSError: Network is unreachable`, the function logs a warning and returns `None`, and the `.local` name never resolves. **The users on exactly the network this feature exists to serve are the ones who lose it**, and the only symptom is one warning line which, per R3, arrives with no timestamp and no module name. Secondary: it picks exactly one IPv4, so a host on both Ethernet and Wi-Fi advertises only one address even though `ServiceInfo` accepts a list. | `ifaddr` 0.2.0 (**MIT**, verified from the venv METADATA). Pure `py2.py3-none-any`, about 10 KB, **zero dependencies**, and **already a required dependency of `zeroconf`** — so it is installed on every taOS box today. `ifaddr.get_adapters()` enumerates every interface and its addresses with no network I/O and no default-route assumption. | **S** | **REPLACE** — keep the existing probe as first choice, fall back to `ifaddr`, and hand `ServiceInfo` the full address list. |
| **R16** | Network subprocesses have no timeout | 32 `create_subprocess_exec` calls in the top-level modules; 18 do a bare `await proc.communicate()`. Network-facing: `catalog_sync.py:22` (clone), `:35` (pull), `auto_update.py:160-169` (the shared `_run`, used for `git fetch`), `desktop_rebuild.py:117` (`npm ci`), `gpg_verify.py:116` (keyserver fetch) | `git` has no wall-clock timeout of its own (only `http.lowSpeedLimit`, unset by default), so a stalled TLS connection to a mirror leaves the background update task awaiting `communicate()` **forever**. The task is never cancelled and never logs, and every subsequent update-check tick either piles up behind it or is skipped. The same applies to the initial catalog clone, which is on the **first-boot** path — a first boot behind a captive portal can hang the catalog sync task for the life of the process. `agent_bridge.py:179` and `:260` show the correct pattern in this very repo (`asyncio.wait_for(proc.communicate(), timeout=…)`) — the idiom exists, it was just never applied to the network calls. | **None needed** — `asyncio.wait_for(...)` plus `proc.kill()` in the `TimeoutError` handler. `anyio.fail_after` is available (anyio is already installed) but buys nothing here. | **S** | **KEEP the stdlib approach, FIX the missing timeouts** — add a `timeout` parameter to the shared `_run` helpers and pass a value at the five network sites first; the local ones are far lower risk. |
| **R17** | Fifteen byte-identical framework adapters | `tinyagentos/adapters/` — zeroclaw, nullclaw, shibaclaw, picoclaw, nanoclaw, moltis, microclaw, ironclaw, agent_zero, langroid, smolagents, openai_agents_sdk, pocketflow, hermes, openclaw. 26–51 LOC each, roughly 500 LOC total | **No defect found — maintenance-only win**, but a compounding one: each file carries its own copy of `_RETRY_KWARGS = dict(max_attempts=7, base_delay=0.5, multiplier=2.0, max_delay=60.0)` and its own `_controller_post` with `timeout=60`. **Every defect in R4 is replicated fifteen times**, the 450-second worst-case retry budget has to be fixed in fifteen places, and a fix applied to one adapter and not the others is invisible to review. | **None** — this is an in-repo factory, not a dependency: `make_proxy_adapter(framework=…, env_var=…, default_url=…, path=…)` returning a configured `FastAPI` app, plus a table of the fifteen entries. | **S–M** | **REPLACE with an in-repo factory** — principally so R4's fix lands once instead of fifteen times. Each file is launched as its own `__main__`, so a six-line shim per adapter preserves the module paths. |
| **R18** | WebVTT parser drops hours-less timestamps | `knowledge_fetchers/youtube.py:44-105` (~62 LOC) | `:55-58` require a full `HH:MM:SS.mmm` on both sides of the arrow. **WebVTT makes hours optional** (`MM:SS.mmm --> MM:SS.mmm`), which many caption tools emit for sub-hour media. With such a file no cue matches, the loop walks the whole document, and `parse_vtt` returns an empty list — **an empty transcript reported as success**. The caller at `:223` only catches exceptions, so it is indistinguishable from a video with no captions at all. Also HTML entities are never unescaped after the tag strip at `:72`, so escaped apostrophes and ampersands survive into the index. Separately `:285` scrapes the destination path out of yt-dlp's human-readable stdout instead of using `--print after_move:filepath`. | `webvtt-py` 0.5.1 (**MIT**, verified). Pure `py3-none-any`, **zero deps**, about 30 KB; handles optional hours, cue ids and settings, and `NOTE`/`STYLE`/`REGION` blocks. | **S** | **REPLACE** the cue parsing (keep the YouTube-specific dedup pass at `:82-90`) and add `html.unescape()`. |
| **R19** | APNs mints a JWT per push; 410 treated as plain failure | `push/apns.py:56-68` `build_apns_jwt`, `:85-106` `HttpApnsSender.send` | `:86-89` mints a fresh provider token **on every push**. Apple caps token *generation*; too-frequent minting yields `403 TooManyProviderTokenUpdates` and pushes are refused account-wide under a burst *(Apple docs not re-fetched — **unverified** — but the mint-per-request shape is unambiguous)*. `:106` is `return resp.status_code == 200`, which collapses **410 Unregistered** — the signal to delete a dead device token — into a generic failure, so dead tokens are retried forever and the `apns-id` and `reason` are never surfaced. The ES256 signing itself is correct. | `aioapns` 4.0 (**Apache-2.0**, verified). Pure `py3-none-any`; deps `h2` (MIT, pure), `pyOpenSSL` (Apache-2.0, pulls `cryptography` — already core) and `pyjwt` (MIT, pure). Caches the token with the correct refresh window, keeps one HTTP/2 connection, and surfaces `reason`. Lighter but weaker: `PyJWT` 2.13.0 alone fixes neither the caching nor the 410. | **S–M** | **WRAP** — the `ApnsSender` Protocol already isolates this cleanly. If the dependency is unwanted, the two must-fix behaviours (cache the JWT for about 50 minutes; delete the token on 410) are roughly 20 lines and should land regardless. |
| **R20** | Storybook PDF wrap splits on spaces | `projects/storybook.py` (178 LOC) — `_wrap` `:56-71`, `_render_page` `:129-152` | `:58` is `words = (text or "").split()`. **CJK and Thai have no spaces**, so the caption becomes one "word", the `else` branch appends nothing, and the oversize string is drawn at `:145` clipped at the page edge. There is also no line-count clamp: `block_h = len(lines) * line_h` and the loop at `:144-146` walk past the page height. Output is raster-only, so no selectable text and large files. | `uniseg` 0.10.1 (**MIT**, verified). Pure `py3-none-any`, **zero deps**, about 200 KB, UAX #14 line breaking; feed `line_break_units(text)` into the existing `draw.textlength` loop — roughly 10 lines, keeps Pillow. `reportlab` 5.0.1 (BSD-3, mature since 2000) for real text PDFs, but 5.x pulls `rl_accel`, `rlPyCairo`, `freetype-py` and `uharfbuzz` — several native, and **ARM64 wheel availability was not individually verified**. | **S** (uniseg) / **L** (reportlab) | **WRAP with `uniseg`** plus an overflow clamp; **KEEP** the Pillow rasteriser — the module docstring makes the offline / no-new-deps constraint explicit. |
| **R21** | 66 hand-rolled modals, one use of the installed Radix Dialog | 66 files contain `role="dialog"`; 50 carry `aria-modal`. Exactly one imports `@radix-ui/react-dialog` (`components/AgentKillSwitch.tsx`). Representative: `apps/ProjectsApp/InviteAgentDialog.tsx:176-177`, `apps/StoreApp/LicenseAcceptDialog.tsx:54`, `apps/StoreApp/PermissionConsent.tsx`, `apps/LibraryApp.tsx`. Several hundred LOC of repeated overlay and close handling. Plus `hooks/use-focus-trap.ts:1-45`, used by 3 files | `InviteAgentDialog.tsx:176` asserts `aria-modal="true"` to assistive tech while leaving the whole background tree focusable and readable — no focus trap, no Escape, no focus restore, no `inert`. A screen-reader user tabs straight out into the dock: a **false a11y claim, which is worse than no attribute**. No `overflow:hidden` on body either, so the desktop scrolls behind every modal on mobile. And the hook that was meant to help is **inert in the common case**: `use-focus-trap.ts:3`'s `input:not([disabled])` matches `<input type="hidden">` and every `display:none`/`hidden`/`inert` descendant, so `:18`'s `focusable[0]!.focus()` is a silent no-op; `:38-39` bind the keydown listener to the container rather than `document`, so once focus is outside the Tab handler **never fires**; `:22-33` take first and last in DOM order, ignoring positive `tabindex`; `:41` restores focus to a possibly-detached node. | `@radix-ui/react-dialog` 1.1.23 (**MIT**, verified) — **already a declared dependency**, matching the `^1.1.23` already in `package.json`. WAI-ARIA compliant, very high download volume, pure JS. **Bundle cost: zero** — broad adoption costs roughly the shared Radix primitives already pulled by `react-dropdown-menu`, `react-tabs` and `react-switch`. Its embedded FocusScope deletes `use-focus-trap.ts` outright. (`focus-trap-react` 12.0.3, MIT, ~6 kB, only if a non-dialog trap survives.) | **L overall, perfectly incremental** | **REPLACE**, starting with the consent and permission dialogs — 66 independent ~20-line swaps. Risk: Radix portals to `document.body`, so z-index needs re-checking against `components/Window.tsx`. |
| **R22** | `navigator.clipboard` unguarded on a plain-HTTP origin | 20 `navigator.clipboard` occurrences in non-test sources (verified on `origin/dev`). Representative: `components/CodeBlock.tsx:14`, `components/TaosAssistantPanel.tsx:619`, `apps/AgentMessagesPanel.tsx:64`, `apps/MCPApp.tsx:1163`, `apps/MessagesApp.tsx:1897,1906`, `apps/ClusterApp.tsx:300`, `apps/ProvidersApp.tsx:753`, `apps/LoRAStudioApp.tsx:80`, `apps/SettingsApp/UsersPanel.tsx:43` | taOS is reached over plain HTTP on the LAN — **not a secure context, so `navigator.clipboard` is `undefined`**. `CodeBlock.tsx:14` throws a `TypeError`. `ClusterApp.tsx:304` and `UsersPanel.tsx:43` `.catch()` it, so the button *appears* to work and copies nothing. Exactly one file gets it right: `components/InstallHelperPanel.tsx:49-50` checks `navigator.clipboard && window.isSecureContext` and falls back to `execCommand` — and its comments document this precise scenario, which is evidence the bug is real rather than theoretical. | `clipboard-polyfill` 4.1.1 (**MIT**, verified), **zero runtime deps**, about 2 kB gz — handles the secure-context gate, the `execCommand` fallback, Safari's user-gesture rule and `ClipboardItem`. **Cheaper:** one internal `lib/clipboard.ts` generalising the already-correct `InstallHelperPanel` logic — the logic exists and is right; the defect is that it lives in one file instead of `lib/`. | **S** | **REPLACE** (or WRAP with the internal helper) — 20 one-line sites. |
| **R23** | `MessagesApp` WebSocket leaks a self-reconnecting zombie | `apps/MessagesApp.tsx` `connectWs`; reconnect at `:1256-1260`, cleanup at `:1281-1286` | `onclose` schedules `setTimeout(connectWs, delay)` (verified at `:1260`) and **never stores the handle**; the cleanup only neutralises the *currently open* socket. Sequence: backend restarts, `onclose` sets `wsRef.current = null` and arms the timer, the user closes the window, cleanup sees `null` and does nothing, the timer fires, a socket opens for an unmounted component, its `onclose` re-arms — **a permanent reconnect loop against the Pi**, with `onmessage` holding stale `setState` closures. Also no jitter, so N tabs reconnect in lockstep after a Pi reboot; no max-attempts; and the `readyState <= 1` guard lets the zombie block a genuine remount. | `partysocket` 1.3.0 (**MIT**, verified), dep `event-target-polyfill`, optional peer react>=17; actively maintained, ships an unmount-safe `useWebSocket`, about 4 kB gz. Alternative `reconnecting-websocket` 4.4.0 (MIT, zero deps, ~2 kB, drop-in API) but frozen and low-activity. Both give cancellable backoff, jitter, maxRetries and a `close()` that actually stops. | **S–M** | **REPLACE** for MessagesApp (prefer `partysocket`). **KEEP `TerminalApp`** — a shell session must not silently reconnect to a new PTY, and it correctly prints "Connection closed" at `apps/TerminalApp.tsx:307`. |
| **R24** | SSE: 3 of 8 consumers never reconnect, 2 duplicate the backoff | Full reconnect+backoff+dedupe **duplicated** about 60 LOC each in `hooks/use-event-stream.ts:41-118` and `hooks/use-os-events.ts:20-130` (the `MAX_SEEN_IDS = 128` ring buffer and the backoff constants are copy-pasted). **No reconnect at all:** `hooks/use-desktop-command-stream.ts`, `apps/ProjectsApp/canvas/canvas-sse.ts:18-45`, `lib/projects.ts:446`, `apps/FilesApp.tsx:698-700`, `apps/MCPApp.tsx:1136`, `apps/SettingsApp/LogsPanel.tsx:174` | Verified on `origin/dev`: `use-desktop-command-stream.ts:118-121` is an **empty `es.onerror`** whose comment claims *"On a hard close the effect's cleanup runs and a remount opens a fresh subscription"* — but the effect's dependency array is `[]` and the hook mounts **once in the app shell** and never remounts. `use-event-stream.ts:33-38` documents the exact opposite: *"an HTTP error response (e.g. a 401 after session expiry) closes the connection for good."* Both cannot be right. A 401 or a controller restart leaves `readyState === CLOSED`, the browser gives up, nothing remounts, and **the agent's desktop-control channel is dead for the session** — open-app, window, screenshot and layout requests all stop, silently. Given that the agent drives the OS only through this channel, this is the highest-impact silent failure in the desktop slice. `canvas-sse.ts:41-44` carries the same wrong assumption for collaborative canvas edits. | **None better than native `EventSource`.** `eventsource-parser` 4.1.0 (MIT, zero deps, ~1.5 kB) is only relevant for fetch-based SSE and does not reconnect. `@microsoft/fetch-event-source` is MIT but last published 2022 — a maintenance liability. The correct fix is **internal**: promote `use-os-events.ts`'s implementation (it handles the `CONNECTING` state correctly and exposes `connected`/`stale`) into one `lib/sse.ts`. | **M** | **KEEP native `EventSource`, REPLACE the duplication with one internal module.** The one finding in the desktop slice where the answer is not a third-party package. |
| **R25** | 14 relative-time formatters, none guards a negative diff | `apps/LibraryApp.tsx:217-224`, `apps/RedditApp.tsx:61-68`, `apps/XApp.tsx:71`, `apps/YouTubeApp.tsx:53`, `apps/ProjectsApp/elements/ElementCard.tsx:5-12`, `components/NotificationCentre.tsx:21-27`, `apps/AgentMessagesPanel.tsx:50`, `apps/NotificationArchiveApp.tsx:12`, `apps/FilesApp.tsx:111-124`, `apps/BrowserApp/AgentPanel.tsx:37`, `apps/MailApp/index.tsx:77`, `apps/SettingsApp/LogsPanel.tsx:27`, `apps/SettingsApp/AccountPanel.tsx:27`, `lib/youtube.ts:105`. About 110 LOC; three are byte-identical. Plus roughly 70 raw `toLocale*` sites | Every copy computes a difference against `Date.now()` and branches on `diff < 60`. **None guards `diff < 0`.** A server timestamp slightly ahead of the browser clock — routine on an RTC-less Pi before NTP converges — makes `apps/RedditApp.tsx:64` print **`-3m ago`**. `FilesApp.tsx:111` returns an em dash for `ts === 0` but every other copy renders the epoch as `20289d ago`. No pluralisation, no locale. | **`Intl.RelativeTimeFormat`** (ES2020, baseline everywhere React 19 runs) — handles the sign, pluralisation and `numeric:"auto"` (so "yesterday"), in the user's locale. **Licence n/a; bundle cost 0 bytes.** Beats `date-fns` (~12 kB gz with a locale) and `dayjs` (~3 kB plus a plugin) on a Pi-served SPA. | **M** | **REPLACE with `Intl` behind one internal helper.** Explicitly **do not** add date-fns or dayjs. Tests asserting `"5m ago"` need updating, or keep the terse format and centralise only the sign and zero guards. |
| **R26** | Shortcut registry has no input-field guard | `hooks/use-shortcut-registry.tsx:1-135`, 20 registrations (`App.tsx:88-169`, `SearchPalette.tsx:35`, `Launchpad.tsx:36`) | The handler is bound to `window` (`:91`) and calls `preventDefault()` plus `stopPropagation()` on any match (`:84-86`) with **no `e.target` check**. `App.tsx:93` registers `Ctrl+f`, so **Ctrl+F in the chat composer or in CodeMirror maximises the window instead of finding text**; the same for `Ctrl+w`, `Ctrl+m` and `Ctrl+l`. `:21` conflates `e.ctrlKey`/`e.metaKey`, which is wrong on the shipped macOS app. `parseCombo` (`:12-17`) only knows ctrl/shift/alt, so `parseCombo("meta+k")` returns `key: "meta"` and **silently registers a shortcut that can never fire**. No `e.isComposing` check, so shortcuts fire mid-IME-composition. Minor: `:121` evaluates `Math.random()` every render, and `formatCombo` never shows Cmd in the help sheet. | `tinykeys` 4.0.0 (**MIT**, verified), **zero dependencies**, about 400 bytes gz. Handles `$mod` (Ctrl on Windows/Linux, Cmd on macOS), sequences and correct key semantics — ideal for a Pi bundle. Alternative `hotkeys-js` (MIT, ~5 kB) has a built-in form-field filter. | **M** | **WRAP** — keep the `useShortcut` API and the scope-priority layer, back parse and match with `tinykeys`, and add the `e.target` form guard, which is the biggest win and free. |
| **R27** | 529 fetch sites, no timeout anywhere, a monkey-patched `window.fetch` | 529 `fetch(` occurrences excluding tests. Exactly one typed wrapper (`lib/projects.ts:186-202`, 17 LOC). Cross-cutting concerns are implemented by **patching the global**: `lib/auth-guard.ts:51-114` replaces `window.fetch` to inject CSRF and dispatch session-expired on 401. `if (!res.ok) throw` repeats roughly 89 times with a different error shape each time | **No request has a timeout.** There is no `AbortSignal.timeout` anywhere in non-test sources; the roughly 20 `AbortController` uses are debounce-cancellation, not timeouts. With the backend on a Pi that can stall under model load, a hung request spins a component forever with no error path. Second: `hooks/use-session-persistence.ts:74-158` — five restore calls do `.then(r => r.json())` **without checking `r.ok`** and `.catch(() => {})`. A 500 returning an HTML error page makes `r.json()` throw, the empty catch swallows it, and the user's saved windows, dock, wallpaper and widgets silently do not restore, with no signal anywhere. | `ky` 2.1.0 (**MIT**, verified), **zero runtime deps**, about 4 kB gz. Gives `timeout` (10 s default), `retry` with exponential backoff on idempotent methods only, `beforeRequest`/`afterResponse` hooks — exactly where CSRF injection and 401 handling belong, replacing the monkey-patch — typed `.json<T>()`, and an `HTTPError` carrying the response. | **L nominally, but staged** | **WRAP.** The value lands without touching all 529: one `lib/api.ts` `ky` instance with the CSRF hook, the 401 hook and a default timeout; migrate the roughly 40 `lib/*.ts` clients (they already centralise most traffic); delete the global patch last. **Zero-dependency stopgap available today: add `AbortSignal.timeout()` inside the existing monkey-patch.** |
| **R28** | 34 polling files, 7 visibility-aware, no request dedup | 45 `setInterval(` across 34 non-test files. Only 7 reference `visibilitychange` or `document.hidden` | `apps/ActivityApp.tsx:253` is `setInterval(fetchData, 2000)` with **no visibility gate**: 30 requests a minute at a 4 GB Pi, forever, hidden tab or not, plus a second interval at 10 s. `apps/MCPApp.tsx:1222` at 3 s, `RegistryPanel.tsx:808` at 5 s. Because each app owns its own interval there is **no dedup** — Activity, Cluster and Agents open together issue three independent polls of overlapping hardware endpoints. The correct pattern already exists in-tree (`use-spa-version-check.ts` documents its visibility gate) and is simply not applied. | `@tanstack/react-query` 5.102.8 (**MIT**, verified), single dep `@tanstack/query-core` (MIT), about 13 kB gz — the largest proposal in the desktop slice, but it deletes more than it adds and directly cuts Pi load. Free: a `refetchInterval` that **pauses when the tab is hidden**, dedup by query key, `staleTime`, `refetchOnWindowFocus`, retry with backoff, and `AbortSignal` on unmount. | **L** | **WRAP/REPLACE, staged**, paired with R27 (`ky` as the fetcher). SSE handlers become `queryClient.invalidateQueries` — a genuine and good architectural change: push invalidation replacing poll. **Immediate zero-dep win: gate the 2 s Activity and 3 s MCP polls on `document.visibilityState === "visible"` today.** |
| **R29** | Both SQL gates parse SQL with regex | `scripts/check_schema_migrations.py:40-70,100-210` (~170 LOC) and `scripts/check_retrofit_migrations.py:52-70,117-175` (~150 LOC). They AST-walk the stores, then regex the `SCHEMA` SQL | All three defects are **false negatives — the gate goes green on a real boot-brick**. `check_schema_migrations.py:42-45` uses `ON\s+(\w+)\s*\(([^)]*)\)`; `[^)]*` stops at the first `)`, so an expression index such as `CREATE INDEX i ON t (lower(name));` captures `lower(name` and the column `name` is never seen as index-referenced — and if `name` is added by a `_post_init` ALTER, that is exactly the boot brick and the gate passes. `:64-67` and `check_retrofit_migrations.py:53` use `ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)`, and `\w+` cannot match a quoted identifier. `:47-50` requires a trailing semicolon on `CREATE TABLE`, so a final unterminated statement yields an empty column set and **inverts** the safety condition. | `sqlglot` 30.18.0 (**MIT**, verified), **zero required runtime deps**, pure Python (the Rust/C accelerators are opt-in extras, so there is no ARM64 wheel concern). A real SQLite-dialect parser; `exp.Create`, `exp.AlterTable`, `exp.Index` plus `.find_all(exp.Column)` handles all three cases. **CI/dev-only dependency — never installed on a Pi.** | **M** (~320 LOC to ~120) | **REPLACE.** Self-verifying migration: diff the violation sets before and after. Call sites are two `main()`s, `doc-gate.yml` and the tests. |
| **R30** | Every macOS release has shipped empty release notes | `mac/build/sparkle_sign.sh:40-61` (~25 LOC), the appcast generator | **Proven by running.** `:48` is `awk -v v="$VERSION" 'BEGIN{p=0} /^## /{p=($2==v)} p' "$NOTES_FILE"` (verified verbatim on `origin/dev`). The changelog headings are `## [1.0.0-beta.50] - 2026-08-21`, so `$2` is the bracketed string and never equals the bare version — the auditor ran it and **the output was empty**. Every appcast item ever produced has a blank `<description>`. Also `${NOTES}` is interpolated into a CDATA block unescaped (a literal CDATA terminator in the changelog breaks the XML), there is no `sparkle:shortVersionString`, and no deltas. | Sparkle's own **`generate_appcast`** — **MIT**, same distribution, 2.6.0 already SHA-pinned at `mac/launcher/Package.swift:13`. Signs, reads versions from the bundle, emits the full appcast and generates deltas *(upstream docs unverified)*. | **S**, one call site (`build.sh:77`) | **REPLACE** — and **even if the script is kept, fix `:48` now**: it is a live user-facing bug on every release. |
| **R31** | Doc gate's `**` matcher is wrong | `scripts/check_doc_gate.py:255-291` `_glob_match` (~35 LOC) | **Proven by executing the real function.** `docs/x.md` against `docs/**/*.md` returns **False** (pathspec: True); `a/b` against `a/**/b` returns **False** (pathspec: True). A mid-pattern `**` compiles to a bare `.*` with the literal slashes intact (`:279`), so `a/**/b` becomes `a/.*/b`. `docs/doc-gate.toml` uses only trailing `**` today, so this is **latent** — but the failure mode is a rule that silently never fires, i.e. a gate going quietly blind. | `pathspec` 1.1.1 (**MPL-2.0** — file-level copyleft, on the allowed list, and CI-only in any case), released 2026-04-27, pure Python, **no required deps**. `PathSpec.from_lines("gitwildmatch", …)` also gives `!` negation and trailing-slash directory rules for free. `PurePath.full_match()` is not an option (3.13+; taOS supports 3.11). | **S**, two callers | **REPLACE** |
| **R32** | `git --name-status` parsed by hand in four gates | `_parse_name_status` duplicated near-verbatim in `scripts/check_doc_gate.py:384-397` and `check_store_wiring.py`; `check_all_skip.py` and `check_deleted_symbols.py:147-176` scrape git separately | **No call passes `-z` or `-c core.quotePath=false`.** git quotes non-ASCII paths by default, so a path containing an accented character comes back octal-escaped **with the quote characters included**, matching no `when_changed` or `satisfied_by` glob — **any path with a non-ASCII character silently escapes every doc rule**. `check_doc_gate.py:394`'s `parts[-1]` is right for a rename record but a path containing a literal TAB shifts the split. And `check_store_wiring.py:96` runs a class-definition regex over raw diff text, so a `+` line that is merely a *comment or string* mentioning the class counts as a definition, and it cannot tell one file's hunk from another's. | **Zero-dep and unconditional:** `git -c core.quotePath=false diff -z --name-status`. For the hunk-level check, `unidiff` 1.0.0 (**MIT**, verified, released 2026-07-25, **no runtime deps**). `GitPython` (BSD-3, unverified) is not worth it. Also extract the copy-pasted `_run_git`/`_parse_name_status` into one `scripts/_gitutil.py`. | **S** | **REPLACE the quoting regardless; WRAP the hunk check with `unidiff`** |
| **R33** | Installer greps JSON and awks YAML with a parser already on the box | `scripts/install-server.sh:2211-2240`; `scripts/fs-snapshot-install.sh:14-15,42` | `grep -q '"vulkan":[[:space:]]*true'` matches **anywhere in the body**, so a `true` on any nested or secondary device sets the host flag; a pretty-printed response makes every check silently read false and skips verification with no warning; the `"type": "rknpu"` grep matches any key named `type` anywhere. The comment at `:2223` says grep was chosen "to avoid a dependency" — but by `:2211` the venv python has already been built and import-verified. Separately `fs-snapshot-install.sh:42` pipes `incus storage show` through `awk -F': '`, which takes the first `source:` at any nesting depth and truncates on a `: ` inside a path (`incus storage get` exists and returns exactly that field); and `:14-15` takes the **alphabetically first** pool, so on a host with both a default pool and a taOS pool, **snapper is configured on the wrong filesystem and a layer of the recycle-bin protection guards nothing**. | **None — zero new dependency.** One `./.venv/bin/python -c 'import json,sys; …'` for the JSON; `incus storage get` for the YAML. | **S** | **REPLACE the parsing** |
| **R34** | `worker_disk_cap()` dies mid-provisioning on `100G` | `scripts/install-worker.sh:138-155`, consumer at `:243` | `value="${override%[GTM]B}"` strips only an uppercase `G`, `T` or `M` followed by `B`, and the `case` labels are the exact strings `GB`/`TB`/`MB`. So `100gb` or `100G` falls into the empty branch and is echoed as a byte count; the caller then divides it in bash arithmetic, **and the script dies under `set -euo pipefail` mid-provisioning with no explanation**. The `*)` "unsupported unit" `die` written to catch exactly this is unreachable for those inputs. | **None** — `numfmt --from=iec` (coreutils, already required since `df --output` on the next line is GNU-only) handles every form and errors loudly. | **S** | **REPLACE the parser** |
| **R35** | Download+checksum copy-pasted about 12 times; 89 curls, 4 retries | Independent sha256 helpers in `install-llama-cpp.sh:75-83`, `install-ollama.sh:26`, `install-lcm-dreamshaper.sh:65`, `install-sd-cpp.sh:46-51`, `install-musicgpt.sh:54-59`, `install-piper.sh:38-43`, `install-tailscale.sh:41-47` and `:77-80`, `install-rknpu.sh:140-143`, `taos-deploy-helper.sh:64-67`, `install-server.sh:1445-1455`, `mac/build/build_python.sh:38-43`, `fetch_container_cli.sh:35-40`. Four are Linux-only (`sha256sum` unconditional) | **89 `curl` invocations, 4 with `--retry`.** One dropped TCP connection aborts an install on domestic wifi — and this is the "must just work on a fresh Pi" path. Separately, `build_python.sh:38` and `fetch_container_cli.sh:35` string-compare the contents of a checksum file against a `shasum`-plus-`awk` pipeline; the two checksum files are bare 64-hex today (verified, 65 bytes) so it works, but regenerate one with the natural `sha256sum f > x.sha256` and **every build fails with a confusing "SHA mismatch"**. | **None** — `curl --retry 5 --retry-all-errors --retry-delay 2 -C -` is the whole fix, and `sha256sum -c` / `shasum -a 256 -c` accept the standard format. `install-server.sh:1443` `_try_prebuilt_bundle` (verify-then-extract, staged in-tree, atomic rename-out and rename-in with a real restore path) is the in-repo model to copy. | **M** | **REPLACE with one shared helper** plus the curl flags |
| **R36** | GitHub REST and Link pagination hand-rolled twice | `scripts/check_bot_review.py:85-127` and `check_gate_integrity.py:97-142` — the same roughly 45 LOC; the second docstring even says "matching check_bot_review.py's contract" | **No rate-limit handling**: GitHub's secondary limit (403/429 plus `Retry-After`) maps to `return None`, which means "cannot see", which means a red required check — the exact failure class `check_bot_review.py` exists to prevent. `link.split(",")` is not an RFC 8288 parser (latent; GitHub's next-links are comma-free today). `check_bot_review.py:118` reads `r.headers` after the `with urlopen()` block has closed the response — it works on CPython by implementation detail. And `RATE_LIMIT_RE:55-62` uses `\s*` between words, so a **hyphenated** "rate-limited" matches nothing; re-verify against the bot's literal stub text. | `gh api --paginate` (**zero dep** — already used at `ci.yml:231`, preinstalled on runners, and it handles `Retry-After`) or `ghapi` 2.1.3 (**Apache-2.0**, verified, released 2026-09-01, pure Python). **`PyGithub` is a licence BLOCKER: LGPL-3.0** *(unverified — confirm before anyone reaches for it)*. | **S–M** | **WRAP.** Lowest-risk first step: de-dupe into one `scripts/_ghapi.py` and add `Retry-After` handling there. |
| **R37** | 275 catalog manifests validated by `isinstance` ladders | `scripts/check_manifests.py:37-118` (~80 LOC) plus `scripts/audit-manifests.py:43-190` (~150 LOC) plus the runtime loader. `find app-catalog -name manifest.yaml` returns **275** | `check_manifests.py:61` is `if not isinstance(lifecycle, dict): continue` — a manifest whose `lifecycle:` is a list or a string is **silently skipped, not reported**, so a typo'd manifest passes the gate. `audit-manifests.py:78/88/116/177` have the same shape. The contract `check_manifests.py` enforces is otherwise careful (it even flags a truthy-but-not-`True` `auto_manage`) — **the defect is the absence of one schema, not a bug in the file.** | `pydantic` v2 — a hard transitive dep of FastAPI, so a `Manifest` model costs **zero new bytes on the Pi** and becomes the single source of truth for CI and the runtime. `jsonschema` if a language-neutral `manifest.schema.json` for third-party app authors is wanted (see R14 for its weight). | **L** | **REPLACE** — expect grandfathering (there is already a `GRANDFATHER` dict at `:31`). Coordinate with R14; it is the same model. |
| **R38** | No linter at all | `ci.yml:155-174`'s entire `lint` job is `compileall`. Grepping `pyproject.toml`, all 18 workflows, `.githooks/*` and `desktop/package.json` for ruff, shellcheck, flake8, black, mypy, eslint or prettier returns **zero hits** | **5,988 lines of root-running installer bash have never seen shellcheck.** Several defects in this audit are free linter catches: the unquoted variable at `install.sh:141` (SC2086), `install-server.sh:1230`'s unguarded arithmetic on a command substitution, `bump_version.sh`'s write to a non-existent directory, and the dead `_unused_have_root_or_sudo()` at `install-worker.sh:1329`. | `ruff` 0.16.6 (**MIT**, verified, no deps, **aarch64 manylinux wheels published**) and `shellcheck` — **GPL-3.0** *(unverified)*, and **not a blocker**: it is an external analysis binary invoked as a separate CI process, never linked and never shipped, so it places no obligation on the AGPL-plus-commercial artifact. **Do not vendor it.** Both are dev/CI-only. | **S to start** | **ADD** — start at `ruff --select=E9,F` and `shellcheck --severity=error`. Best risk-removed-per-effort ratio in the tooling slice. |
| **R39** | Four divergent systemd units for one service | `scripts/systemd/tinyagentos.service` (canonical: `User=taos`, `.venv/bin/python`); `install.sh:177-194` heredoc (**`User=root`**, `/opt/tinyagentos`, **`venv/bin/python`**); `systemd/tinyagentos.service`; the repo-root `tinyagentos.service` (**hardcodes a developer's username and home path**); `os-build/.../tinyagentos.service` (`taos`, but `venv/bin/python`) | `install.sh:3` still documents itself as the pipe-to-shell entry point while `README.md:76` points at `install-server.sh`. An `install.sh` install therefore gets `User=root` (defeating all of `ensure_taos_user`), a `venv/` path that `install-server.sh` will not find, apt's Node 18 (the thing `ensure_node22` exists to avoid), and **no SPA build at all — the UI is simply missing**. Grepping the canonical unit for `Protect*`, `NoNewPrivileges`, `PrivateTmp` or `RuntimeDirectory` returns **0 hits**, and `Restart=on-failure` has no `StartLimitIntervalSec`. `install.sh:139-142` uses `cp` plus `chmod` (with `:141` unquoted) where `install-server.sh` correctly uses `install -m 0755`. | **None** — one template plus `.service.d/` drop-ins for the `Environment=` lines, replacing the fragile `sed -i` at `install-server.sh:1911` (which also breaks on a pipe or ampersand in the install path), and `systemd-analyze verify` in CI. Make `install.sh` a shim that `exec`s `install-server.sh`. | **M** | **CONSOLIDATE**, and add the missing hardening directives while there |
| **R40** | `disk-quota-scan.sh` is a permanent silent no-op | `scripts/disk-quota-scan.sh` (20 LOC) — token read at `:7`, bail at `:11-14`, port at `:19` | Verified on `origin/dev`: `:7` reads the auth token from a **developer's home-directory path** (and a legacy `/opt` path), while the modern installer defaults to a third location — so the token is never found. `:13` then prints a message and **`exit 0`s**, i.e. a green oneshot that did nothing. `:19` hardcodes the port, ignoring `TAOS_PORT` — the very mistake `taos-graceful-stop.sh:11-13` documents having already fixed once. The units are installed only by the legacy `install.sh:158-168`; `install-server.sh` never installs them at all. And a developer's home path is committed in a public repo. | **None** | **S** | **Fix and wire in, or delete.** It should `exit 1` so the timer shows in `systemctl --failed` — the project's own "fallback must fail, not narrate" rule. |
| **R41** | Three changelog writers, three incompatible formats | `scripts/collate_changelog.py` (~200 LOC, `## [ver] - date`); `scripts/gen-release-notes.sh` (78 LOC, conventional commits); `mac/build/bump_version.sh:24-34` (`## X.Y.Z`, no brackets or date) | A `## 1.0.0` heading is invisible to `collate_changelog.py`'s `version_header = f"## [{args.version}]"` guard, so a later collate writes a **second section for the same version**. The parser itself: **no defect found — maintenance-only win**; the risk is the fragmentation. Separately `mac/build/bump_version.sh` is **dead and broken**: `:21` writes into a `frontend/` directory that does not exist (it is `desktop/`), so under `set -euo pipefail` it aborts; `:14` writes an uncommitted, unread `.version`; and grepping the whole repo finds `bump_version` only inside the file itself. | `towncrier` 25.8.0 (**MIT**, released 2025-08-30, pure Python). Deps `click` plus `jinja2` — and **jinja2 is already a core taOS dep**, so the marginal cost is `click`. Needs `[tool.towncrier] directory = "changelog.d"` to preserve the existing paths that `.githooks` and `docs/doc-gate.toml` reference. `bump-my-version` 1.5.1 (**MIT**) handles `beta.50 → beta.51` and can drive `uv.lock`'s normalised `1.0.0b50` from the same config — a known repeat footgun here. All dev-group. | **M** / **S** | **REPLACE** (dev-only). Delete or rewrite `bump_version.sh` — it has zero call sites. |
| **R42** | Two Radix packages installed with zero imports | `desktop/package.json:30` `@radix-ui/react-select` and `:34` `@radix-ui/react-tooltip`. Grep across `desktop/src` for both specifiers (static and dynamic) returns **zero hits**, while 39 files use a raw `<select>` and 3 hand-roll `role="tooltip"` | Not a runtime defect — cost with no benefit, plus a missed win. A native `<select>` cannot be theme-styled, and on iOS Safari (the shipped PWA surface) it renders as a system wheel ignoring the theme tokens, so the 39 sites look wrong on exactly the platform the mobile components target. | Already installed, both **MIT**; zero new install weight (about 5 kB gz Select, 3 kB Tooltip on top of Radix primitives already bundled). | **S** (delete) or **L** (adopt) | Replace the 3 hand-rolled tooltips with the installed Tooltip. For `<select>`, either commit to it on the mobile-visible surfaces or **delete the dependency** — shipping unused deps on a Pi is the one outcome to avoid. |
| **R43** | No list virtualisation anywhere | None installed, none hand-rolled. Full renders at `apps/chat/MessageList.tsx:373`, `apps/MessagesApp.tsx`, `apps/FilesApp.tsx:1008`, `apps/LibraryApp.tsx:901`, `apps/agents/AgentTracesPanel.tsx:86` | **No defect found — scaling win only.** `MessageList.tsx:373` runs a full `react-markdown` plus `rehype-highlight` pipeline per message, so a several-thousand-message channel locks the render thread. The *client* browser pays this, not the Pi, so it ranks below R28. | `@tanstack/react-virtual` 3.14.10 (**MIT**, verified), one MIT dep, headless with dynamic measured row heights and `scrollToIndex`, about 3.5 kB gz. | **M** per list | **REPLACE for `MessageList` only. KEEP elsewhere** until a list is demonstrably slow. Risk: interacts with scroll-anchoring and the jump-to-message highlight. |

---

## 4. Zero-dependency quick fixes

Everything here is worth doing **regardless of whether any library above is adopted**. Most are one
to five lines. They are grouped by how much they buy.

### 4.1 Two lines each, high value

| Fix | Where | Why |
| --- | --- | --- |
| Derive the OTel span id the same way as the parent ref | `otel/emitter.py:104` — `span_id = sha256(env["id"])[:8].hex()` instead of `_make_span_id()` | Makes roughly 870 LOC of tracing actually nest (R11) |
| Drain MCP child stdout | `mcp/supervisor.py` — a `_drain_stdout` task mirroring the existing `_drain_stderr` | Prevents a hard hang reported as "running" (R10) |
| Add `PRAGMA busy_timeout = 5000` to the async pragma helper | `db_migrations.py:249-253` | Fixes 75 stores at once and lets three ad-hoc workarounds be deleted (R6) |
| Delete the partial file in the download error arm | `download_manager.py:299-303` — `task.dest.unlink(missing_ok=True)` | Stops a corrupt weight file sitting at the canonical path (R9) |
| Bound the event-bus queues | `events/bus.py:56` — `asyncio.Queue(maxsize=N)` plus a drop policy in `_publish_to_channel` | Removes a slow OOM path on a 4 GB Pi (R12) |
| Widen the retry exception tuple and re-raise the original | `clients/retry.py:25-29` and the `_StatusError` arm | Covers `ConnectTimeout`/`PoolTimeout`/`ReadError`; stops a private type escaping as a 500 (R4) |
| ~~Fix the eval detector's lookbehind~~ **done (tsk-lpdd2e)** | `code_analyzer.py:135` — also match `\b(?:window\|globalThis\|self)\.eval\s*\(` | Closes an indirect-eval hole in a security gate (S8) |
| Fix the Sparkle awk field | `mac/build/sparkle_sign.sh:48` — match the bracketed heading, not `$2` | Every macOS release since the beginning has shipped blank release notes (R30) |

### 4.2 Small, mechanical, still real

- **Configure logging.** One `dictConfig` in `create_app()`/`main()` with a `root` logger. Without
  it every finding whose only symptom is a log line is invisible (R3).
- **Route the five byte-size parsers at the one correct implementation**
  (`routes/agent_images.py::_parse_size_bytes`) and add a `T`/`TiB` branch — that alone fixes
  `_parse_memory("512m") → 0` and the never-enforced quota above 1 TiB (R1).
- **Use `packaging.version`** in `worker/update_check.py` and `routes/apps.py` (R2).
- **Point the nine hand-rolled temp-plus-replace writers at `atomic_io`** (R7), then add the CI grep
  that bans a tenth.
- **Add `asyncio.wait_for` to the five network subprocess calls** (R16).
- **Give `themes/package.py` the member-count and size limits `userspace/package.py` already has**,
  and cap the upload in `routes/themes.py:29` (S7).
- ~~**Backport `routes/peer.py`'s `_RATE_HITS_MAX_SIZE` eviction** into `rate_limit.py` and
  `auth_middleware.py`, and switch the fixed windows to `time.monotonic()`~~ — **done** (S5):
  all five limiters now run on the bounded, monotonic `MovingWindowLimiter`/`RateLimiter`
  in `tinyagentos/rate_limit.py`, without the `limits` dependency.
- **Honour `Retry-After` on Discord 429** and **move the Slack cursor advance after dispatch** —
  about five lines each, independent of any library (R13).
- **Cache the APNs JWT for about 50 minutes and delete the device token on 410** — roughly 20 lines
  (R19).
- **`git -c core.quotePath=false … -z`** in all four gate scripts (R32).
- **Replace the installer's JSON grep with `python -c 'import json…'`** and the incus YAML awk with
  `incus storage get`; pick the pool explicitly rather than alphabetically (R33).
- **`numfmt --from=iec`** in `worker_disk_cap()` (R34).
- **`curl --retry 5 --retry-all-errors --retry-delay 2 -C -`** across the 89 download sites, and
  `sha256sum -c` instead of string-comparing hashes (R35).
- **`disk-quota-scan.sh` should `exit 1` on a missing token**, read `TAOS_PORT`, and take its path
  from the installer default — or be deleted (R40).
- **Add a `flock "$INSTALL_DIR/.taos-update.lock"` wrapper**: there is **no `flock` anywhere in the
  repo**, so `bin/update.sh`, an installer re-run and the in-app self-update can race on one tree.
- **Stop `rollback.sh` sourcing a data file** (S10) and move the graceful-stop stamp to a
  `RuntimeDirectory` (S11).
- **A logrotate drop-in on `data/controller.log`** — the only unbounded log; everything else goes to
  journald, but the no-systemd `nohup` fallback path writes there forever.

### 4.3 Desktop, zero bundle bytes

- **Guard `diff < 0` and `ts === 0`** in the 14 `timeAgo` copies, and **clamp the unit index** in the
  8 `formatBytes` copies (R25, R1).
- **Delete `apps/LibraryApp.tsx:185`** — it *fabricates* a byte count from a string hash
  (`(Math.abs(hash) % 4000 + 100) * 1024 * 1024`) and renders it indistinguishably from a real
  measurement. That is a fabricated measurement, not a formatting problem.
- **Add the `e.target` form-field guard** to the shortcut registry — the single biggest win in R26
  and free.
- **Gate the 2 s `ActivityApp` and 3 s `MCPApp` polls on `document.visibilityState`** (R28).
- **Add `AbortSignal.timeout()` inside the existing `window.fetch` monkey-patch** — gives every one
  of the 529 call sites a timeout today without the `ky` migration (R27).
- **Check `r.ok` before `r.json()`** in `hooks/use-session-persistence.ts:74-158` — five calls
  silently lose the user's saved windows, dock, wallpaper and widgets on a 500 (R27).
- **`use-clock.ts`**: drop the hardcoded `en-GB` at `:11,14` and re-arm the tick on the minute
  boundary (`60_000 - (Date.now() % 60_000)`) instead of a free-running 30 s interval — the top-bar
  clock is currently stale by up to 30 s, so on average it is wrong half the time.
- **Give `lib/slug.ts` a non-empty fallback** and collapse the divergent copy at
  `components/ConsentActions.tsx:30-32` into it (R5).
- **Promote `dompurify` from `overrides` to `dependencies`** (S12) — one line.
- **`components/Desktop.tsx:125`** does `JSON.parse(localStorage.getItem(...) || "[]")` inside a
  `.catch()`, so a corrupt value throws an unhandled rejection and the snippet is lost. One-line
  try/catch.
- **`new Intl.Collator(undefined, {numeric:true})`** at `apps/FilesApp.tsx:1010` and
  `VfsBrowser.tsx:82` — plain `localeCompare` sorts `file10` before `file2`. Native, zero cost.
- **The 12 inline `Math.random().toString(36)` id sites** (`MessagesApp.tsx:852,1764,2206,2371`,
  `ContactsApp.tsx:16`, `AssistantStudioApp.tsx:94`, `BrowserApp/AgentPanel.tsx:186,211`,
  `gamestudio/games-api.ts:86`, `musicstudio/types.ts:69`) should just call the existing
  `lib/uid.ts` `randomId()`. All are local-only keys, so this is hygiene, not a defect.

### 4.4 Python correctness nits found in passing

- `unique_agent_slug` (`config.py:267`) appends a numeric suffix to an already-63-character slug,
  overrunning the container-name limit the truncation exists to respect.
- `verify_password`'s legacy SHA-256 branch (`auth.py:167`) compares with `==` rather than
  `secrets.compare_digest`, unlike every other comparison in that file.
- Session tokens are stored **in plaintext** as the JSON keys of `.auth_sessions`
  (`auth.py:748-757`), and broker virtual credentials are stored in plaintext as a SQLite primary
  key (`broker/store.py:43`) — while `agent_model_key_store.py:55` gets it right and stores
  `sha256(token)`. One store hashes, two do not.
- `computer_use.py:96` does an unguarded `int(coords[0])`, so a malformed action line from an LLM
  raises `ValueError` out of the parser.
- `shortcuts/token_source.py`'s hand-rolled RFC 6901 JSON Pointer is correct (including the `~1`
  before `~0` escaping order) except that `pointer == "/"` returns the whole document where RFC 6901
  says it addresses the empty-string key.
- `chat/context_window.py:20` estimates tokens as `len(text) // 4`, which under-counts CJK by two to
  four times, so a Chinese or Japanese history can overflow the window `history_token_budget()`
  sized for it. No library recommended — `tiktoken` downloads BPE files at first use and breaks
  offline-first; use the backend's own `/tokenize`, or count CJK code points at roughly one token.
- `scheduler/task_runner.py` calls `croniter(schedule, base)` with a float epoch base, which
  evaluates in **UTC**, so a user's "back up at 3 AM" fires at 3 AM UTC. A product decision, not a
  library one.
- `routes/images_edit.py:241-248` and `asset_gen/comfyui_client.py:49-61` duplicate 4-byte image
  magic sniffing and have **already drifted** (one accepts GIF, the other does not). One shared
  helper — or `PIL.Image.open()` in a try/except, since Pillow is core and actually validates
  structure — is simpler than adding `filetype` or `puremagic`.
- `event_stream.py:89` emits an SSE `id:` while ignoring `Last-Event-ID`, whereas `os_events.py:20-21`
  deliberately omits `id:` for exactly that reason. The former makes browsers send a header that is
  discarded. Pick one.

---

## 5. KEEP — custom code that is correctly hand-rolled

Consolidated from all five slices' "checked, keep as-is" sections. These were examined and are
justified; none needs a library.

**Crash-safety, crypto and identity**

- `atomic_io.py` — better than the deprecated `atomicwrites` package: fsyncs the temp file *and* the
  parent directory, randomised temp name, `O_EXCL`, partial-write loop, mode applied before the
  rename, narrow errno allowlist for mounts that cannot fsync a directory. Exemplary. The only
  problem is that nothing but `auth.py` uses it (R7).
- `log_redaction.py` — pattern plus known-secret-value redaction, fails closed on short values,
  never widens a match to swallow context. `scrubadub` and friends are PII-oriented and heavier.
- `gpg_verify.py` — shells out to `git verify-commit`/`verify-tag`, i.e. uses real GPG rather than
  reimplementing signature checking. (Nit: `_validate_keyserver` uses a `startswith` plus
  banned-character check instead of `urlsplit`; nothing exploitable found.)
- `store_signing.py`, `agent_registry_store.py` mint/verify, `push/apns.py` ES256 signing,
  `github_app.py` — all sign and verify via `cryptography`, already a direct dependency.
  `verify_registry_token` is safe against an `alg:none` forgery because it ignores the header
  entirely and verifies against a pinned Ed25519 key; the missing `exp` claim is compensated by
  `agent_token_auth.py:115-119`'s status check and `token_min_iat` rotation cutoff. `PyJWT` would
  consolidate roughly 120 LOC of base64url/claims glue across those four files and give `exp`/`nbf`
  and `alg` pinning for free — a clean **maintenance-only WRAP with no defect behind it**.
- `secrets.py` — Fernet from `cryptography`, with a documented one-way migration off a legacy
  format; the legacy branch is dead in practice because `key_dir` is always supplied.
- `shared_folders.py` — path traversal guarded by reducing to `Path(...).name` after normalising
  backslashes, applied on create **and** re-applied on read and delete to cover legacy rows. Better
  than a `..` blacklist.
- `desktop_rebuild.py:167-172` — `tarfile.extractall(..., filter="data")` with an explicit refusal
  (falling back to a local build) on Pythons that lack the filter. Exactly right.
- `middleware/csrf.py` bearer and websocket exemptions — correct; only the token binding is weak (S6).
- `lib/csrf.ts` (SPA) — small, correct double-submit helper. `lib/uid.ts` — `crypto.randomUUID()`
  with an explicit non-secure-context fallback; `nanoid` would add a dep for the same result.
- `lib/auth-guard.ts` — the monkey-patch is architecturally unfortunate (R27) but careful:
  `isSameOrigin` parses the URL rather than prefix-matching, and the Request-object header merge
  documents the bug it fixes. Keep until R27 lands.

**Data, scheduling and storage**

- `broker/`, `metrics.py` and the whole `*_store.py` family — thin, fully parameterised `aiosqlite`
  over `BaseStore`. No ORM is warranted; SQLAlchemy would be a large install-weight regression on a
  4 GB target.
- `projects/task_store.py` — status transitions expressed as atomic SQL `WHERE status = …` guards.
  Race-free by construction; a state-machine library would be a downgrade.
- `notes/shared_docs_store.py` — already uses `diff-match-patch` with checkpointed replay. There is
  no hand-rolled diff anywhere in the tree.
- `projects/routines_store.py` and `scheduler/task_runner.py` — already use `croniter`, including
  `croniter.is_valid()` at write time so a bad schedule fails at create rather than at fire.
- `scheduler/gpu_arbiter.py`, `scheduler/core_aware_scheduler.py`, `scheduling/leases.py` — bespoke
  VRAM and lease arbitration with renewal and loss detection. No library models this domain.
- `chat/peer_outbox.py` and `backend_fallback.py` — backoff is persisted (`next_retry_at`) or
  clock-based, so `tenacity` would not fit.
- ID generation (`design_docs.py:14`, `install_registry.py:9`, `receipt_store.py:35`,
  `board_audit.py:9`, `coding_workspaces.py:11`) — `secrets.choice` over a Crockford-style base32
  alphabet with bounded collision retry. Correct; the only issue is that the same five-line helper
  is copy-pasted five times.
- Timezone handling across the backend is consistently `datetime.now(timezone.utc)`; there is no
  `utcnow()` anywhere. `python-dateutil` or `whenever` would add nothing.

**Protocol and integration**

- `containers/{docker,lxc,__init__}.py` — drive the CLIs with `--format {{json .}}` / `-f json` and
  parse **JSON, not regex**. The `docker` SDK adds `requests`, does not cover podman or incus, and
  needs socket access. CLI-plus-JSON is the better fit here.
- `routes/desktop_browser/rewriter.py` (HTML half) — an `lxml.html` DOM walk, not regex. Only the
  CSS half is a problem (S3).
- `routes/desktop_browser/cookie_jar.py` — bridges `httpx.Cookies` and stdlib `http.cookiejar`
  rather than parsing `Set-Cookie` by hand. Drops `HttpOnly`/`SameSite`, documented — a fidelity
  gap, not a library one.
- `store_popularity.py` — reads `X-RateLimit-Remaining`/`X-RateLimit-Reset`, arms a real back-off
  window, and distinguishes transient from permanent failure for cache-TTL purposes. **The one
  place in the codebase that handles a remote rate limit correctly**, and the model R13's connectors
  should copy.
- `mail_client.py` — stdlib `email`/`imaplib`/`smtplib`, RFC 2047 headers decoded via
  `make_header(decode_header(...))`, charset-aware payload decoding, a CR/LF/NUL header-injection
  guard, STARTTLS honoured on both IMAP and SMTP. No defect found.
- `notifications_push.py` — `pywebpush` and `py-vapid` for Web Push; no hand-rolled ECE encryption.
- `services/mdns_publisher.py` — uses `zeroconf` with `AsyncZeroconf` and relies on the library's
  own `allow_name_change` collision handling. Only `_detect_primary_ipv4` is a problem (R15).
- `knowledge_fetchers/github.py` — plain `httpx` against the GitHub API; PyGithub is sync, heavy and
  LGPL, githubkit adds pydantic weight. Roughly 400 narrow, readable LOC. (No `Retry-After` handling
  found — worth a separate look.)
- `taosnet/*`, `routes/a2a_bus.py`, `projects/a2a.py` — taOS-private protocols over HTTP/JSON;
  nothing off-the-shelf applies.
- `chat/mentions.py`, `projects/beads_format.py` — small anchored regexes over a taOS-private
  grammar. `otel/trace_context.py` — W3C `traceparent` construction, four lines, correct.
- `desktop_control/broker.py` and `chat/chat_exporter.py`'s Kahn-style same-timestamp causality sort
  — small, correct, library-free.
- The 14 SSE endpoints — hand-rolled frames, but done well (keepalive, `is_disconnected()`, a clean
  `finally` unsubscribe). `sse-starlette` would dedupe them (R12) but there is no defect to fix.
- `cli/taosctl/` — plain `argparse` with auto-discovered noun modules and documented exit codes
  (0 success / 1 transport / 2 API). `typer` or `click` would add a dependency for no capability
  gain. `client.py` uses stdlib urllib rather than the already-core `httpx`, documented as "runs
  anywhere with zero extra dependencies" — the rationale is partly moot but still right for agent
  containers. `output._render_table` measures column width with `len()`, so CJK and emoji cells
  misalign and a JSON-encoded dict cell emits a multi-KB line; `tabulate` (MIT, pure, optional
  `wcwidth` — exactly the CJK case) is the library, but it would land in the **core** dep list
  because `taosctl` is a `[project.scripts]` entry, so every fresh Pi would pay for a cosmetic bug.
  **KEEP.**
- `hardware.py`, `system_stats.py` — `/proc` and `/sys` probes for the RK3588 NPU, Mali/panthor,
  thermal zones and the device-tree model. Board-specific; no library covers this, and `psutil`
  (already a dependency) is used wherever it applies.

**Desktop**

- `components/Window.tsx` uses the installed `react-rnd`; no parallel drag implementation exists.
  `hooks/use-snap-zones.ts` — 82 LOC of window-snap geometry; no library does OS snap zones, and the
  ref-based stable callbacks correctly avoid the react-rnd controlled-position jump.
- `stores/process-store.ts` — window-manager state including `safeBounds` off-screen reclamping.
  Domain-specific.
- `shell/dnd/*` (145 LOC) — native HTML5 DnD bus; the dragenter/dragleave counter is the standard
  workaround and the 30 s stale-payload timer is thoughtful. `@dnd-kit` would lose cross-iframe drags.
- `apps/designstudio/useElementHistory.ts` — undo/redo in 62 LOC, both stacks capped, redo cleared on
  commit. `zundo`/`immer` would add weight for no gain.
- Markdown — `react-markdown` + `remark-gfm` + `rehype-highlight` + `rehype-slug`, used properly.
  **No hand-rolled markdown parser anywhere.** Sanitising — `ReaderMode.tsx:34` uses DOMPurify with
  `USE_PROFILES:{html:true}` on the only untrusted `dangerouslySetInnerHTML`, and `MermaidBlock.tsx:27`
  sets `securityLevel:"strict"`. Both correct; only the packaging is wrong (S12).
- Colour maths — only `theme-store.ts:392-405`, 14 LOC. `chroma-js`/`culori` would be 15–40 kB for
  one light/dark decision. Unhandled formats fall through to `"dark"`, a safe default.
- Zustand usage — no hand-rolled subscription plumbing; `use-drop-target.ts` uses
  `useSyncExternalStore` and `browser-settings-store.ts:27` uses the built-in `persist`.
- `hooks/use-list-nav.ts` — 30 LOC roving index, correct modular arithmetic.
  `components/WidgetLayer.tsx` uses the installed `react-grid-layout`.
- `apps/TerminalApp.tsx` — deliberately does **not** auto-reconnect (`:307`). Correct for a PTY.
- Form validation — 82 hits, all small field-level predicates; no hand-rolled schema engine, so
  `zod` would be a new ~14 kB dep for a problem the SPA does not have (the backend validates with
  Pydantic). i18n — roughly 20 inline `x !== 1 ? "s" : ""`; `Intl.PluralRules` is the zero-cost
  answer when localisation lands.
- Zero `JSON.parse(JSON.stringify())`, `deepEqual`, `isEqual` or `structuredClone` anywhere; zero
  IndexedDB usage, so `idb` has nothing to wrap; no hand-drawn chart code; no grapheme or
  surrogate-pair splitting.
- `three@0.185.1` with no `src` import is **not dead** — it pins the version of the two vendored
  `public/gamestudio-seeds/*/three.module.js` copies. Correct but undocumented; write it down.

**Tooling**

- `install-server.sh:169-262` `ensure_node22()` — NodeSource repo with an explicit GPG fingerprint
  pinned and verified *before* import, per package manager. **Better than the official convenience
  script; do not replace.**
- `install-server.sh:1420-1500` `_try_prebuilt_bundle()` — the best code in the tooling slice and
  the template R35 should copy. `ensure_taos_user()` — `useradd -r -M` with a nologin fallback and
  getent-guarded group adds.
- `.github/workflows/ci.yml` — pinned actions, `uv sync --frozen`, `pytest-split` with a verified
  union, an `if: always()` aggregate gate with documented reasoning. No marketplace action
  re-implemented in shell. `pr-base-guard.yml` and `dependabot-automerge.yml` use SHA-pinned
  official actions.
- `check_gate_integrity.py` — the `pull_request_target` plus base-ref design is the right fix for
  the class of defect it documents; only its HTTP layer needs work (R36).
- `check_deleted_symbols.py` — `git merge-tree --write-tree` plus `tarfile` over `git archive`, and
  `ast` rather than regex for symbols. Correct tool choices throughout.
- `check_dependency_audit_ignores.py` — wraps `pip-audit` and `uv lock --upgrade-package` rather
  than reimplementing either. Exactly the right shape.
- `os-build/build.sh` — Armbian pinned by tag **and** verified against an immutable commit SHA to
  defeat tag retargeting. Exemplary.
- `rebuild-desktop.sh` — a `find -newer -print -quit` staleness check with a keep-the-old-bundle
  failure path. `bin/update.sh` — correct `git checkout --` / `git clean` of build outputs before
  `pull --ff-only`.
- `install-git-hooks.sh` — uses `git config core.hooksPath`, not symlink hackery. `pre-commit` (MIT)
  would be the library answer but adds a dependency for two hooks.
- `scripts/install-worker.ps1` — idiomatic `[CmdletBinding()] param()`, winget,
  `Register-ScheduledTask`. `cli/taosctl/argtypes.py`, `cli/worker.py` — proper argparse with
  subparsers and `type=` validators; no hand-rolled arg parsing anywhere.
- `tinyagentos/__main__.py` — env/config resolution plus `_NoSignalServer` dual-port serving;
  legitimately custom, since uvicorn has no public dual-bind API.
- `mac/.../ServerProcess.swift` — `Process` plus `URLSession` polling, 101 LOC, nothing
  reimplemented; Sparkle used rather than a hand-rolled updater.
- TLS — no certificate generation anywhere in the tooling slice; the only `openssl` call is
  `openssl rand -hex 24` with a `/dev/urandom` fallback. Correct.
- Backups — only `pre-beta-to-beta.sh:211` (a `tar -czf` of `data/` before a one-shot migration).
  restic (BSD-2) / borg (BSD-3) *(both unverified)* are licence-clean but unwarranted for one
  pre-migration snapshot.
- `build-routes-doc.py` and `build-agent-manual.py` are near-identical (identical function
  inventories, differing only in source dir, output and header). **No defect found —
  maintenance-only win**; the risk is drift.

**Config**

- `config.py`'s env and YAML loading — `pydantic-settings` (MIT, pure) would suit the 31 scattered
  `os.environ.get` reads and would catch a non-numeric `TAOS_PORT` (today an uncaught `ValueError`
  traceback) and a quoted port in YAML (today reaching uvicorn as a string). But taOS's config is a
  **mutable, self-migrating YAML document the app rewrites at runtime**, and a settings library is
  the wrong shape for that. Recommend instead making `AppConfig` a pydantic `BaseModel` — pydantic
  is already present, the same move as R14 — and leaving the env reads alone. Maintenance win, no
  new dependency.

---

## 6. Rejected libraries, and why

| Library | Reason |
| --- | --- |
| `borb` | **AGPL-3.0 — BLOCKER.** Cannot be conveyed under the commercial half of the dual licence. |
| `fpdf2` | **LGPL-3.0 — FLAG.** Do not adopt for a dual-licensed commercial distribution without legal sign-off. |
| `semgrep` (OSS) | **LGPL-2.1 — FLAG**, plus a large OCaml binary. Unsuitable for a Pi. |
| `PyGithub` | **LGPL-3.0 — BLOCKER** *(unverified — confirm before anyone reaches for it)*. `gh api --paginate` or `ghapi` (Apache-2.0) instead. |
| `python-slugify[unidecode]` | The extra pulls **`Unidecode`, GPL-only — BLOCKER**. The plain package is MIT and fine (see §2.2). |
| `esprima` (Python) | Last release **2018-08-24**, unmaintained, no ES2020+. Use tree-sitter if a JS parser is needed. |
| `@microsoft/fetch-event-source` | MIT but last published **2022** — a maintenance liability on a security-relevant path. |
| `starlette-csrf` | MIT but last released **2023-06-27**; stdlib `hmac` gives the same property with no dependency. |
| `atomicwrites` | Explicitly unmaintained and deprecated by its own author, **and weaker** than the in-repo `atomic_io` (no parent-directory fsync). |
| `advocate` (SSRF guard) | Unmaintained, pins old `requests` *(unverified)*. |
| `humanfriendly` | MIT and correct, but **last release 2021-09-17**. Accepted with a caveat in R1; a stdlib unit table is the lighter trade for one `parse_size` call. |
| `huggingface_hub` | Apache-2.0 and does everything R9 needs, but **112 `requires_dist` entries** — far too heavy for the 4 GB core. |
| `tiktoken` | MIT, but **downloads BPE files at first use**, which breaks offline-first unless vendored. |
| `date-fns` / `dayjs` | MIT, but roughly 12 kB / 3 kB gz for what `Intl.RelativeTimeFormat` does natively at **0 bytes** on a Pi-served SPA. |
| `chroma-js` / `culori` | 15–40 kB for one light/dark decision made in 14 LOC. |
| `zod` | ~14 kB for a problem the SPA does not have — the backend validates with Pydantic. |
| `@dnd-kit` | Would lose the cross-iframe drags the native HTML5 DnD bus supports. |
| `alembic` | MIT, but requires SQLAlchemy plus Mako — a large install-weight and conceptual cost for a raw-`aiosqlite` codebase on a 4 GB target. |
| `yoyo-migrations` | Apache-2.0 and much lighter, but file-based, sync-only, and has no notion of several namespaces sharing one connection. |
| `docker` SDK | Adds `requests`, does not cover podman or incus, and needs socket access. The CLI-plus-JSON approach is a better fit. |
| `GitPython` | BSD-3 *(unverified)* but not worth it for four gate scripts; `unidiff` covers the actual need. |
| `nanoid` | Would add a dependency for what `crypto.randomUUID()` with a fallback already does. |
| `rich` / `tabulate` in `taosctl` | Would land in the **core** dep list (`taosctl` is a `[project.scripts]` entry) so every fresh Pi pays, for a cosmetic column-alignment bug. |
| `restic` / `borg` | BSD, licence-clean *(unverified)*, but unwarranted for one pre-migration snapshot. |
| `reportlab` | BSD-3 and mature, but 5.x pulls `rl_accel`, `rlPyCairo`, `freetype-py` and `uharfbuzz` — several native, **ARM64 wheel availability not individually verified**. |
| `jsonschema` | MIT and fine, but pulls `attrs`, `referencing` and **`rpds-py` (Rust)**. pydantic is already present and generates the JSON Schema anyway. |
| `slowapi` | MIT, but a much smaller and less active project than `limits`; take `limits` alone and keep the roughly 20 lines of taOS-specific middleware. |
| `stamina` | MIT, but it is a thin wrapper over `tenacity` — a second package for no extra capability. |
| `shellcheck` | **GPL-3.0** *(unverified)* — **accepted anyway** as an external analysis binary invoked as a separate CI process, never linked and never shipped. **Do not vendor it.** |
| `pathspec` | **MPL-2.0** — file-level copyleft, on the allowed list, and CI-only. **Accepted.** |

---

## 7. Coverage — and what was not reached

Stated honestly, because a "clean" section of an audit means nothing without knowing whether it was
read.

### Read fully / verified against live registries

- **Licences:** `pyproject.toml`; `uv.lock` (all 172 `[[package]]` blocks parsed, every name+version
  queried against PyPI's per-version JSON, cross-checked against 161 installed `dist-info/METADATA`
  files); `desktop/package.json`; `desktop/package-lock.json` (1024 entries → 926 unique
  `name@version`, all 5 blanks and 7 non-SPDX strings resolved); `LICENSE`,
  `COMMERCIAL-LICENSE.md`, `CLA.md`, the README licence section; tldraw's `LICENSE.md` at v4.5.12
  and tldraw.dev/pricing; Simple Icons, dashboard-icons, Sparkle 2.6.0 and python-build-standalone
  `LICENSE` files; the installed `sqlcipher3` wheel (licence file plus `strings` on the 20.3 MB
  `.so`).
- **Python core:** `atomic_io.py`, `db_migrations.py`, `base_store.py`, `rate_limit.py`,
  `clients/retry.py`, `backend_fallback.py`, `download_manager.py`, `events/bus.py`,
  `channel_hub/webhook_connector.py`, `scheduler/task_runner.py`, `cli/taosctl/__main__.py`,
  `cli/taosctl/output.py`, two representative adapters, `worker/update_check.py:135-215`. `config.py`
  in full (510 lines).
- **Python domain:** `containers/backend.py`, `themes/{package,schema}.py`,
  `userspace/{package,url_guard}.py`, `projects/canvas/{unfurl,render}.py`,
  `projects/{beads_format,lifecycle,storybook}.py`, `chat/{context_window,mentions}.py`,
  `mcp/{proxy,supervisor}.py`, `middleware/csrf.py`, `otel/{trace_context,emitter}.py`,
  `push/apns.py`, `taosnet/{passkey_client,torrent_client,mesh_credentials}.py`,
  `routes/desktop_browser/{ssrf,cookie_jar,rewriter}.py`, `knowledge_fetchers/youtube.py` (VTT half),
  `code_analyzer.py`, `conversion.py`, `design_docs.py`, `office_docs.py`, `app_orchestrator.py`,
  `computer_use.py`, `download_manager.py` download paths.
- **Desktop:** the eight hooks named in §3, `lib/{csrf,auth-guard,uid,slug}.ts`,
  `apps/ProjectsApp/canvas/canvas-sse.ts`, `apps/designstudio/useElementHistory.ts`, `shell/dnd/*`,
  `desktop/package.json`.
- **Tooling:** `install.sh`; `bin/update.sh`; both `.githooks/`; all five systemd unit files;
  `os-build/build.sh`; ten `scripts/*.sh` in full; all nine `check_*.py` gates plus
  `.github/scripts/check_all_skip.py`; the six `mac/build/*.sh` scripts;
  `.github/workflows/{ci,security,pr-base-guard,dependabot-automerge}.yml`;
  `cli/taosctl/{client,output}.py`. `_glob_match` and `sparkle_sign.sh`'s awk were **executed**, not
  just read.

### Swept by grep/find across the whole tree

Vendored directories, copyright notices, SPDX headers, attribution phrases, minified bundles, fonts,
audio, model binaries; retry and backoff loops; subprocess timeouts; `os.environ` reads; datetime and
timezone usage; `urlsplit`/`urlparse`; `zipfile`/`tarfile`/`extractall`; `fcntl`/`flock`; `/proc` and
`/sys` parsing; `hmac`/`compare_digest`; `text/event-stream` producers; `basicConfig`/`dictConfig`;
atomic-write patterns; `BaseStore` subclasses (78 found); `save_config` references (85 found). On the
desktop side: relative-time and byte formatting, SSE and WebSocket clients, fetch wrappers and
abort/timeout, virtual lists, drag/resize/window management, keyboard shortcuts, fuzzy search, form
validation, `dangerouslySetInnerHTML` and sanitising, colour maths, id generation, deep-equal/clone,
localStorage/IndexedDB, file-type sniffing, pluralisation, clipboard, focus traps, dialog/menu/tooltip
primitives, toast queues, undo/redo, table sorting, chart code, emoji handling, polling intervals,
plus a full installed-vs-imported dependency reconciliation.

### Not reached — would repay a second pass

These were grepped but not read line by line. They are the honest gaps.

| Area | Why it matters |
| --- | --- |
| `cluster/manager.py` (1183 LOC) | The largest unread domain module; cluster lifecycle and node state |
| `routes/{agents,projects,cluster}.py` (1500+ LOC each) | Grepped only. The four rate limiters and 14 SSE producers cited in §3 live partly here |
| `library_pipeline.py` + `knowledge_ingest.py` | Likely chunking and MIME logic of exactly the kind §3 keeps finding |
| `skills.py`, `agent_registry_store.py` (46 KB each) | Only the mint/verify regions were read |
| `app.py` (1904 LOC) | Skimmed as route wiring; the `recover-password` CLI verb found in R8 came from here, so a full read may find more |
| `llm_proxy.py`, `litellm_*`, `restart_orchestrator.py`, `update_runner.py`, `torrent_downloader.py`, `expert_agents.py`, `opencode_runtime.py`, `browser_sessions.py`, `deployer.py`, `trace_store.py`, the `knowledge_*` modules | Docstring and structure only |
| `otel/{receiver,span_store,judge}.py` | Only the emitter half of the OTel stack was read in full; R11's fix touches the receiver too |
| App-catalog manifest **content** (275 files) | The validators were read, not the data |
| `mac/launcher/Sources/*.swift` beyond `ServerProcess.swift` | Skimmed |
| `security/`, `benchmarks/`, `tests/`, `docs/`, `landing/`, `site/` | Out of every slice |
| The studio apps — `officestudio`, `musicstudio`, `gamestudio`, `designstudio` beyond the history hook, `codingstudio`, `webstudio`, `videostudio`, `images`, `theme/effects` | Integrations of already-chosen libraries; skipped deliberately |

### Explicitly out of scope

Patent exposure; export control; trademark clearance for the taOS mark itself; the licences of the
134 catalog apps beyond confirming they are separately installed; per-image provenance of the 16
store covers; the exact sample sets `smplr` fetches at runtime; whether the `aarch64-apple-darwin`
python-build-standalone build links libedit or readline.

---

## Appendix — verified licence tables

Trimmed to the rows that carry a verdict other than plain OK, plus the direct dependencies that
matter. The full 172-row Python table and 926-package npm histogram were produced during the audit.

### A.1 Python — every non-OK row from `uv.lock`

| Package | Version | Licence (SPDX / declared) | Scope | Verdict | Note |
| --- | --- | --- | --- | --- | --- |
| `litellm-enterprise` | 0.1.51 | LicenseRef-Proprietary (LiteLLM Enterprise) | transitive of `litellm[proxy]` | **BLOCKER** | Installed on every server by `scripts/install-server.sh:1510` |
| `zeroconf` | 0.150.0 | LGPL-2.1-or-later | **direct, core** | **FLAG** | Unconditional import at `services/mdns_publisher.py:29` |
| `pystray` | 0.19.5 | LGPL-3.0-or-later | extra:worker | **FLAG** | Import guarded at `worker/tray.py:23`, still shipped |
| `python-xlib` | 0.33 | LGPL-2.1-or-later | transitive | **FLAG** | Transitive of `pystray` on Linux |
| `soundfile` | 0.12.1 | BSD-3-Clause | transitive (proxy) | **FLAG** | Wheels bundle libsndfile (LGPL-2.1) |
| `text-unidecode` | 1.3 | Artistic-1.0 OR GPL-2.0-or-later | transitive | **FLAG** | Artistic arm usable; the election must be recorded |
| `sqlcipher3` | 0.6.2 | MIT *(declared)*; ships a Zlib-style pysqlite LICENSE; bundles SQLCipher (BSD-3) + OpenSSL 3.6.0 (Apache-2.0) | **direct** | **FLAG** | 20.3 MB static blob; only the pysqlite notice ships |
| `lxml` | 6.1.1 | BSD-3-Clause | direct | **FLAG** | manylinux wheels statically bundle libxml2/libxslt |
| `pillow` | 12.3.0 | MIT-CMU | direct | **FLAG** | Wheels bundle libjpeg-turbo/zlib/libtiff/libwebp/freetype |
| `numpy` | 2.4.6 | BSD-3 AND 0BSD AND MIT AND Zlib AND CC0-1.0 | transitive | **FLAG** | Bundled components |
| `onnxruntime` | 1.26.0 | MIT | transitive | **FLAG** | MIT wrapper over many notice-bearing components |
| `orjson` | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) | transitive | **FLAG** | |
| `certifi` | 2026.5.20 | MPL-2.0 | transitive | **FLAG** | Also redistributes the Mozilla CA bundle |
| `pycryptodome` | 3.23.0 | BSD-2-Clause AND Unlicense | transitive | **FLAG** | Via matrix-nio |
| `restrictedpython` | 8.3 | ZPL-2.1 | transitive (proxy) | **FLAG** | Permissive, BSD-like, non-standard |
| `chardet` | 7.4.3 | 0BSD | transitive | **FLAG** | Was LGPL-2.1+ through 5.2.0 — version-sensitive |
| `tinyagentos` | 1.0.0b50 | AGPL-3.0-or-later | this project | OK | but `pyproject.toml:9` does not declare it as an SPDX expression (§2.6) |
| `taosmd` | 0.4.0 | MIT | direct | OK | Deliberate? See §2.6 item 3 |

**Clean:** the remaining 154 of 172 locked packages are permissive/OK, including every other entry
in `pyproject.toml` — fastapi, uvicorn, httpx, matrix-nio (ISC), readability-lxml, croniter,
diff-match-patch, pywebpush/http-ece/py-vapid (MPL-2.0), cryptography, aiosqlite, argon2-cffi,
psutil, pyyaml, python-multipart, websockets, jinja2 (BSD-3, unused — §2.7), `libtorrent` (BSD-3,
`torrent` extra), `prisma` (Apache-2.0) and `litellm` itself (**MIT**).

### A.2 JavaScript — non-OK rows and the resolved ambiguities

| Package | Version | npm `license` | Actual | Verdict |
| --- | --- | --- | --- | --- |
| `@tldraw/tldraw` | 4.5.12 | `SEE LICENSE IN LICENSE.md` | `LicenseRef-tldraw`, source-available | **BLOCKER** |
| `@tldraw/assets` | 4.5.12 | `SEE LICENSE IN LICENSE.md` | same — **was Apache-2.0 until 3.12** | **BLOCKER** |
| `@tldraw/{editor,utils,validate}` + `tldraw` | 4.5.12 | `SEE LICENSE IN LICENSE.md` | same | **BLOCKER** |
| `dompurify` | 3.4.13 | `(MPL-2.0 OR Apache-2.0)` | Dual; elect Apache-2.0 | OK — and see S12 |
| `rgbcolor` | 1.0.1 | `MIT OR SEE LICENSE IN FEEL-FREE.md` | Dual; the MIT half is offered | OK — elect MIT |
| `@tsparticles/react` | 3.0.0 | *(absent)* | MIT — repo is MIT, 4.x publishes MIT | OK |
| `fuzzy` | 0.1.3 | *(absent)* | MIT — legacy `licenses:[{type:"MIT"}]` array | OK |
| `jstat` | 1.9.6 | *(absent)* | MIT — same legacy array upstream | OK |
| `khroma` | 2.1.0 | *(absent)* | MIT — repo licence file | OK |

**Whole-tree histogram** (926 unique `name@version`, incl. dev): MIT 797, ISC 47, MPL-2.0 24,
Apache-2.0 22, BSD-3-Clause 9, `SEE LICENSE IN LICENSE.md` 6 (the tldraw family), MISSING 4,
BSD-2-Clause 4, CC0-1.0 3, MIT-0 2, 0BSD 2, `(MIT AND Zlib)` 2, `(MPL-2.0 OR Apache-2.0)` 1,
BlueOak-1.0.0 1, `MIT OR SEE LICENSE IN FEEL-FREE.md` 1, Unlicense 1.

**Specifically checked and clean**, against the watchlist: `@excalidraw/excalidraw` 0.18.1 MIT;
`@excalidraw/mermaid-to-excalidraw` 2.2.2 MIT; `@fortune-sheet/{core,react}` 1.0.4 MIT;
`@milkdown/*` 7.22.0 MIT; **`@tiptap/*` 3.29.2 MIT — none of the five installed extensions is from
the paid Pro tier**; `mermaid` 11.16.1 MIT; `three` 0.185.1 MIT; `tone` 15.1.22 MIT; `plyr` 3.8.4
MIT; `highlight.js` 11.11.1 BSD-3-Clause; `jspdf` 4.2.1 MIT; `mathjs` 15.2.0 Apache-2.0;
`emoji-picker-react` 4.19.1 MIT; `motion` 12.43.0 MIT; `konva`/`react-konva` MIT; `zustand` 5.0.14
MIT; `typescript` 6.0.3 and `@playwright/test` 1.62.1 Apache-2.0.

### A.3 Every library recommended in §3, with its verified facts

| Package | Version | Licence (verified) | Pure Python/JS | Runtime deps of note | Already present? |
| --- | --- | --- | --- | --- | --- |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | yes | none | yes — installed 26.2 |
| `tenacity` | 9.1.4 | Apache-2.0 | yes | none | no |
| `filelock` | 3.32.5 | MIT | yes | none | yes — installed 3.29.4 |
| `ifaddr` | 0.2.0 | MIT | yes | none | yes — core, via zeroconf |
| `pydantic` | v2 | MIT | no (aarch64 wheels) | — | yes — core, via FastAPI |
| `limits` | 5.8.0 | MIT | yes | deprecated, packaging, typing-extensions | no |
| `python-slugify` | 8.0.4 | MIT | yes | text-unidecode (**FLAG**, §2.2) | yes — e2e extra only |
| `sse-starlette` | 3.4.10 | BSD-3-Clause | yes | starlette, anyio (both present) | no |
| `structlog` | 26.1.0 | MIT OR Apache-2.0 | yes | none on py>=3.11 | no |
| `webvtt-py` | 0.5.1 | MIT | yes | none | no |
| `uniseg` | 0.10.1 | MIT | yes | none | no |
| `tinycss2` | 1.5.1 | BSD-3-Clause | yes | webencodings (BSD, pure) | no |
| `humanfriendly` | 10.0 | MIT | yes | none on Linux/py3 | no — **last release 2021** |
| `slack-sdk` | 3.44.1 | MIT | yes | none required | no |
| `discord.py` | 2.7.1 | MIT | yes (dep aiohttp is compiled, aarch64 wheels) | aiohttp | no |
| `aioapns` | 4.0 | Apache-2.0 | yes | h2, pyOpenSSL, pyjwt | no |
| `mcp` (official SDK) | 2.1.1 | MIT | yes | **19 deps incl. httpx2, jsonschema, pydantic, pyjwt[crypto]** | no |
| `opentelemetry-sdk` (+ otlp-proto-http) | 1.44.0 | Apache-2.0 | yes (exporter pulls protobuf) | opentelemetry-proto, googleapis-common-protos, requests | no |
| `tree-sitter` / `-javascript` | 0.26.0 / 0.25.0 | MIT | no — aarch64 wheels, 632 KB + 106 KB | none | no |
| `sqlglot` | 30.18.0 | MIT | yes | none required | no — **CI only** |
| `pathspec` | 1.1.1 | MPL-2.0 | yes | none | no — **CI only** |
| `unidiff` | 1.0.0 | MIT | yes | none | no — **CI only** |
| `towncrier` | 25.8.0 | MIT | yes | click, jinja2 (jinja2 already core) | no — **dev only** |
| `bump-my-version` | 1.5.1 | MIT | yes | click, pydantic, rich, tomlkit, … | no — **dev only** |
| `ruff` | 0.16.6 | MIT | no — aarch64 manylinux wheels | none | no — **dev/CI only** |
| `ghapi` | 2.1.3 | Apache-2.0 | yes | — | no — **CI only** |
| `@radix-ui/react-dialog` | 1.1.23 | MIT | JS | Radix primitives already bundled | **yes — declared, used once** |
| `dompurify` | 3.4.13 | Apache-2.0 OR MPL-2.0 | JS | none | **yes — but only in `overrides` (S12)** |
| `partysocket` | 1.3.0 | MIT | JS | event-target-polyfill | no |
| `tinykeys` | 4.0.0 | MIT | JS | none | no |
| `ky` | 2.1.0 | MIT | JS | none | no |
| `clipboard-polyfill` | 4.1.1 | MIT | JS | none | no |
| `@tanstack/react-query` | 5.102.8 | MIT | JS | @tanstack/query-core | no |
| `@tanstack/react-virtual` | 3.14.10 | MIT | JS | @tanstack/virtual-core | no |

**Every library recommended in this audit is MIT, BSD, ISC, Apache-2.0 or MPL-2.0.** Nothing GPL,
AGPL, LGPL, SSPL, BUSL, Elastic or CC-BY-NC is proposed. The two copyleft entries are `pathspec`
(MPL-2.0, file-level, CI-only) and `shellcheck` (GPL-3.0, an external binary invoked as a separate
process, never linked and never shipped). The one dual-licence election that must be recorded is
`text-unidecode`'s Artistic arm, reached via `python-slugify`.
