# taOS amd64 Installer ISO

## What this ISO is

The taOS amd64 installer ISO is a remastered **official Debian 13 (trixie) netinst ISO** with:
- A taOS preseed file (`preseed.cfg`) for unattended base installation
- A first-boot systemd oneshot unit (`taos-firstboot.service`) that runs the taOS installer on first boot
- The taOS install script (`install.sh`) and first-boot script embedded in the ISO

This is a **rootless build** — it uses `xorriso` to remaster the official netinst ISO, never `live-build` or `debootstrap`. The build runs unprivileged in CI.

## What the installer asks (interactive)

The Debian installer will prompt for:
- **Partitioning** — fully interactive, no preseed. The target machine may hold other operating systems or data disks (e.g., BitLocker). No partman keys are preseeded.
- **Locale and keyboard** — not preseeded
- **Hostname** — not preseeded
- **Root password** — not preseeded
- **First user account** — not preseeded

## What the installer does NOT ask (preseeded)

- Task selection: `standard` + `ssh-server` (no desktop task)
- Extra packages: `curl`, `ca-certificates`, `sudo`, `git` (needed for first boot)
- Package upgrade: `full-upgrade`
- Popularity contest: opted out

## What first boot does

On first boot after installation completes:
1. The `taos-firstboot.service` oneshot runs (condition: `/var/lib/taos/firstboot.done` does not exist)
2. It waits for network (`After=network-online.target`, `Wants=network-online.target`)
3. Runs the embedded taOS install script at `/usr/local/share/taos/install.sh` with `TAOS_DESKTOP=local`
4. Logs to `/var/log/taos-firstboot.log`
5. On success:
   - Creates `/var/lib/taos/firstboot.done`
   - Disables the first-boot service so it doesn't run again
6. On failure:
   - Exits non-zero, leaves the service enabled for retry on next boot
   - Writes the failing step to the log

The installer runs as root (the install scripts require root for package installation, systemd unit management, etc.).

## Building the ISO locally

### Prerequisites

- `xorriso`
- `cpio`
- `gzip`
- Official Debian 13 amd64 netinst ISO (download from debian.org)

### Build command

```bash
cd distro/iso
./build-iso.sh --netinst /path/to/debian-13-amd64-netinst.iso --out taos-amd64.iso
```

### What the build script does

1. Verifies required tools (`xorriso`, `cpio`, `gzip`) are in PATH (exits 2 if missing)
2. Extracts the ISO tree with `xorriso -osirrox on -indev <in> -extract / <tmp>`
3. Makes the tree writable (`chmod -R u+w`)
4. Injects `preseed.cfg` into `install.amd/initrd.gz` (and `install.amd/gtk/initrd.gz` when present) using the Debian-documented method:
   - `gunzip` the initrd
   - `echo preseed.cfg | cpio -H newc -o -A -F initrd`
   - `gzip` the initrd back
5. Copies the repo's `install.sh`, `taos-firstboot.sh`, `taos-firstboot.service` into `<tree>/taos/`
6. Regenerates `md5sum.txt`
7. Repacks the ISO replaying the original boot setup:
   - `xorriso -indev <in> -outdev <out> -map <tree> / -boot_image any replay`
8. All temp directories under `mktemp -d`, cleaned up by `trap`

No `sudo` is used anywhere in the build process.
