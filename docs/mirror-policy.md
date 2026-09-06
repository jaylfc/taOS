# taOS Mirror Policy

taOS's verified install paths depend on binaries — runtime libraries, NPU-converted model weights, firmware — that are not part of any official OS package index. Most of these live in ad-hoc community HuggingFace repos maintained by a single contributor, or on vendor FTPs, or on forums. Any of those sources can disappear, be renamed, or be re-uploaded with different contents at any time, without warning.

To protect the install path from that class of failure, taOS maintains its own binary mirrors. This document describes what we mirror, when we update, how a user can verify integrity, and how the policy applies across every accelerator class we support.

## What we mirror

We mirror **only** the binaries required by a verified install path. We do not mirror the entire Rockchip (or Pi, or Mac mini) ecosystem; we mirror exactly the files that `scripts/install-*.sh` downloads, nothing more. This keeps the mirror scope small, keeps the license story simple, and makes the "what is TAOS responsible for hosting" question easy to answer.

Current mirrors:

| Accelerator class | Mirror repo | Install script |
|---|---|---|
| Rockchip RK3588 | `jaysom/tinyagentos-rockchip-mirror` on HuggingFace | `scripts/install-rknpu.sh` |

As additional accelerator classes are onboarded onto verified install paths (RK3576, Raspberry Pi 4, Mac mini / Apple Silicon, x86 NVIDIA, x86 CPU-only, etc.), each one gets its own dedicated mirror repo following the same shape:

- One HuggingFace repo (or equivalent host) per accelerator class.
- A README on the mirror repo listing every mirrored file, its size, its SHA256, the upstream source, the TAOS version it was verified on, and the verification date.
- A per-class install script under `scripts/` that points at the mirror as its primary source, includes the expected SHA256 as a constant, and hard-fails on checksum mismatch.

The same policy applies to every class. There is no "trust the upstream" tier.

### Documented exception: Hailo `.hef` model binaries (decided 2026-08-10)

The five Hailo-8/10 `.hef` catalog models download from Hailo's own CDN
(`dev-public.hailo.ai`) rather than a taOS-controlled mirror. This is a
deliberate, decided exception, not an oversight: the `.hef` files are
vendor-format binaries for Hailo's own runtime, and redistribution rights for
a public mirror are unverified, so mirroring them would trade an availability
risk for a legal one. Integrity is still covered the same way as everywhere
else: every Hailo manifest pins the file's SHA256 and the download hard-fails
on mismatch. If the CDN ever pulls a file, we mirror at that point, with the
pinned digest proving fidelity to the original. A vendor's own CDN is the one
"upstream" whose identity the no-trust-the-upstream rule was not aimed at;
the pinned hash still removes any need to trust its contents.

## When we update the mirror

The mirror is updated **only after re-verifying the new version end-to-end against a clean install** of taOS on the target hardware. Specifically:

1. A new upstream version is identified (new `librknnrt` build, new `dulimov/*` conversion, new in-house rkllm export, etc.).
2. The candidate file is installed on a clean TAOS image on the target SBC.
3. The full verified install path is exercised — the relevant rkllama / inference server comes up, the preloaded models respond to requests, and any feature that depends on the binary (embeddings, reranking, query expansion, image generation) passes its smoke test.
4. Only after all of the above, the new file is uploaded to the mirror, the SHA256 constants in the install script are bumped, and the README's "Verified on TAOS version" + "Verified date" columns are updated.

Silent upstream drift — upstream changing a file without announcing it — is exactly the failure mode the mirror protects us against. We never blindly re-pull from upstream.

## How a user can verify mirror integrity

Two layers of verification, both covered by the same SHA256 hashes:

1. **Post-download verification in the installer.** Every `install-*.sh` script hardcodes the expected SHA256 for every file it downloads as a constant at the top of the script. After download, the script runs `sha256sum` and hard-fails on mismatch with a clear message. A user running the installer does not need to do anything — a corrupted download or a tampered mirror will be caught before anything is installed.

2. **Manual verification from outside the project.** The same SHA256 hashes are published in the mirror repo README. Anyone, including someone who is not a TAOS user, can:
   - `curl` a file from the mirror.
   - Run `sha256sum` on it.
   - Compare the result against both the script constants and the mirror README.

   All three sources must agree. If they don't, open an issue.

## How to self-host the same mirror

taOS does not want to be a single point of failure. If the upstream HuggingFace repo at `jaysom/tinyagentos-rockchip-mirror` ever becomes unreachable, or an air-gapped deployment needs a local mirror, the process is:

1. Clone the HF mirror repo (`git clone https://huggingface.co/jaysom/tinyagentos-rockchip-mirror`) or use `huggingface-cli download` to pull every file.
2. Host the files on your own HTTP server / S3 bucket / LAN NAS / internal HF instance. Preserve the same relative paths (`librknnrt-...so`, `models/*.rkllm`).
3. In your fork of `scripts/install-rknpu.sh`, change the `TAOS_MIRROR_BASE` constant at the top to point at your host. Leave the SHA256 constants alone — they must still match the canonical file.
4. Run the installer. Verification still works against your self-hosted copy as long as the file bytes match.

Do **not** modify the SHA256 constants when self-hosting. The whole point of the verification layer is to catch unexpected drift, including drift introduced by a well-meaning self-hoster who re-compressed the file. If you need to host a genuinely different file, you are running a different install path and should open an issue upstream.

## Sizing: what belongs in a TAOS mirror

A TAOS mirror repo is for **supply-chain-critical binaries**, not a
convenience model cache. The practical ceiling for a single mirror
repo is:

- **Per-file limit**: 5 GB. HuggingFace's LFS quota per file is 5 GB
  on the free tier; larger files need chunking or a different host.
- **Per-repo limit**: ~50 GB total. Beyond that, clone times become
  painful and the `huggingface-cli download` path gets slow enough
  to frustrate users during install.
- **Per-class scope**: one accelerator class per repo. Do not mix
  Rockchip and Pi binaries in the same repo; the README and the
  self-host path both assume one class per repo.

Rockchip model weights in particular can exceed these limits:
`dulimov/rkllm-*` repos are often ~20 GB per model. **We do not
mirror full model weight catalogues.** We mirror:

- Runtime libraries (`librknnrt.so`, small, ~10 MB)
- One preloaded NPU-converted default model per tier (~2-4 GB)
- Optional quantised variants the install script installs by default
- Firmware / driver blobs if upstream is unreliable

Everything else the user downloads at runtime from wherever the
upstream source is — the mirror is not an attempt to replace HF. It
is an attempt to make *the verified install path* independent of HF's
uptime.

If a binary doesn't fit this policy (e.g. a 40 GB model variant a
user wants as default), the answer is to change the install path
rather than grow the mirror. Tracked in #225.

## Future work: mirror health check

The mirror as it stands is static: files are uploaded, SHA256s are recorded, and we rely on HuggingFace's CDN to keep serving them. The next step is an automated health check job that:

- Runs nightly.
- Walks every mirror repo (not just Rockchip — every accelerator class once more are onboarded).
- Fetches every file listed in its README.
- Verifies each file's SHA256 against the expected value.
- Checks that the URL returns a 200 and not a 404 / 403 / gateway error.
- Surfaces pass/fail status on the public taOS dashboard so anyone can see at a glance whether every verified install path's binary supply chain is currently healthy.

This closes the loop: today the installer hard-fails if a mirror file has drifted, but only at install time. With nightly health checks, we find out before a user ever tries to install.
