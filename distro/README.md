# taOS desktop distro (Debian 13 trixie base)

One setup script is the source of truth: `sudo ./distro/setup.sh` turns a
minimal Debian 13 install into taOS, and the amd64 ISO will run the same
script. It is idempotent, so re-run it after every pull.

Packages live under `packages/<name>/` as plain dpkg-deb trees;
`build-debs.sh` builds them into `dist/` (gitignored).

| Step | Package | What it does |
|------|---------|--------------|
| 1 | taos-branding | os-release NAME/PRETTY_NAME = taOS (ID stays `debian`, VARIANT_ID=taos), console banner, motd, GRUB entry. Debian's files are diverted, regenerated on base-files updates, restored on removal. |
| 2 | taos-shell | zsh for every user (autosuggestions, syntax highlighting, fzf, zoxide, eza/bat aliases), colourful starship prompt with no Nerd Font needed, fastfetch config, gradient taOS logo in the login motd. setup.sh switches existing and new users to zsh. |
| 3 | taos-gpu | `taos-gpu` command: picks the display GPU (integrated by default, NVIDIA cards compute only), generates the /dev/dri/taos-display udev rule, the sway session env and the nouveau/nvidia-drm modprobe config. Choose at install with `TAOS_DISPLAY_GPU` or the prompt. |
| 4 | taos-gpu (driver) | `taos-gpu driver`: official vendor GPU drivers. An NVIDIA GPU gets NVIDIA's official driver from NVIDIA's own apt repo by default; `TAOS_NVIDIA_DRIVER=open` keeps the open-source driver, `skip` leaves it alone. AMD and Intel use the in-kernel driver with Mesa. Handles the Secure Boot key enrollment. |
| 5 | taos-tools | `taos-screenshot [--json] [--output NAME] [file.png]`: grabs the Wayland session with grim, else the console framebuffer with fbgrab. Prints the path (or one JSON object with source, width and height). |
| 6 | taos-power | `taos-power` command: a taOS machine does not sleep by default. The `always-on` profile (default without a battery) never suspends or hibernates; `battery` (default with a battery) allows suspend and changes nothing. An optional CPU tune is off unless chosen. Choose at install with `TAOS_POWER_PROFILE`. |
| 7 | taos-wallpapers | The approved taOS wallpaper set (neural, flow, mesh, terrain), dark and light, pre-rendered JPEG at 3840x2160, 3840x1200 and 1080x2400 under `/usr/share/taos/wallpapers/<id>/` with an `index.json`. `taos-wallpaper list\|path` prints the best file for a size. |
| 8 | taos-desktop-session | The graphical login screen (greetd with gtkgreet in a cage kiosk, VT 7) and the taOS sway session. Installing it does not start the graphical login; `sudo taos-desktop enable` turns it on. |

## Display GPU

Policy `auto` picks an integrated GPU with a connected display, else any integrated GPU, else the boot_vga GPU, else the first GPU. A discrete NVIDIA card is never picked while an integrated GPU exists. NVIDIA cards are compute only: nouveau is blacklisted and nvidia-drm runs with modeset=0 (the proprietary driver still loads for CUDA).

    taos-gpu list [--json]       # GPUs, kind, connectors, display marker
    taos-gpu status [--json]     # policy, display GPU, compute-only GPUs
    sudo taos-gpu set-display auto|0000:00:02.0

Changes need a reboot. The future control panel reads `taos-gpu list --json` and calls `taos-gpu set-display`. The taOS session and greeter read the stable symlink `/dev/dri/taos-display` (made by a udev rule) directly and export its resolved target as `WLR_DRM_DEVICES`.

## GPU drivers

taOS offers official vendor drivers next to the open-source ones. Policy:

- NVIDIA: the official driver comes from NVIDIA's own apt repo (Debian 13, amd64 only) and is installed by default when an NVIDIA GPU is present. Open source (nouveau) is the opt-out: `TAOS_NVIDIA_DRIVER=open sudo ./distro/setup.sh`.
- An NVIDIA card that does not drive the display gets the compute profile (`nvidia-kernel-open-dkms` + `nvidia-driver-cuda`, no X server or display stack). An NVIDIA card that drives the display gets the full profile (`nvidia-open`).
- AMD and Intel: the official driver is the in-kernel one (amdgpu, i915 or xe) with Mesa, so there is nothing to install. `taos-gpu driver install amd|intel` only adds the GPU firmware package. ROCm and the Intel compute runtime are not handled yet.

    taos-gpu driver status [--json]
    sudo taos-gpu driver install nvidia [--branch 620] [--kernel open|closed] [--dry-run]
    sudo taos-gpu driver install cuda [--version 13-5]     # optional CUDA toolkit
    sudo taos-gpu driver remove nvidia                      # back to open source
    sudo taos-gpu driver set nvidia open                    # record the opt-out only

`--dry-run` prints every command it would run. The NVIDIA keyring package is checked against a pinned SHA256 before it is installed. `--branch` pins apt to one driver branch (`nvidia-driver-pinning-<branch>`). From branch 615 NVIDIA only ships open kernel modules; the open modules need a Turing or newer card, older cards need an older branch with `--kernel closed`.

Secure Boot: the driver is built locally (DKMS) and signed with a key made on your machine, which the firmware must trust once. If Secure Boot is on, the install queues that key and prints a one-time password (also saved in `/etc/taos/mok-enroll-password`). On the next reboot a blue "Perform MOK management" screen appears: choose Enroll MOK, Continue, Yes, type the password, then Reboot. If you skip the screen the NVIDIA driver will not load. With Secure Boot off nothing is needed.

`taos-screenshot` exits 1 with a clear message when there is neither a Wayland session nor a framebuffer, and never prints a path that was not written. On a display 1100 px or taller, setup.sh also sets the console font to Terminus 16x32.

## Power

A taOS machine does not sleep by default: it serves models to phones and other devices, so a suspended box is a broken box. `taos-power` has two profiles, stored as `PROFILE=` in `/etc/taos/power.conf`:

- `always-on` (default on any machine without a battery): never suspends or hibernates. It writes `/etc/systemd/logind.conf.d/50-taos-power.conf` (power key, lid switch and idle action are ignored) and `/etc/systemd/sleep.conf.d/50-taos-power.conf` (`AllowSuspend=no` and the other sleep modes). The sleep targets are not masked; removing the files undoes it.
- `battery` (default when a battery is present, scope `Device` peripherals such as a wireless mouse do not count): suspend stays allowed and nothing is tuned; taos-power only removes its own files. Phone power policy belongs to the device layer.

    taos-power status [--json]      # profile and why, tune, battery, suspend policy, CPU state, idle states
    sudo taos-power set always-on|battery
    sudo taos-power tune off|performance
    sudo taos-power set-latency 100  # microseconds, 0 = no latency hold

`always-on` on a battery device is allowed and prints a warning. The control panel will call `taos-power set` (and read `taos-power status --json`).

The CPU tune is separate and optional (`TUNE=off` by default). `performance` sets the EPP (or the governor, where there is no EPP) to `performance` on every CPU that offers it and keeps a PM QoS wake-up latency request (`LATENCY_US`, default 100) alive through the `taos-power` unit, because the request only lasts while `/dev/cpu_dma_latency` stays open. It was measured to make no difference to model decode speed on the reference desktop, which is why it is off unless you choose it. `tune off` stops the holder and writes `balance_performance` back where the CPU lists it; otherwise the kernel default returns on the next boot. It only applies under `always-on`.

## Wallpapers

The set comes from the approved round 2 designs: four wallpapers (neural, the default, then flow, mesh and terrain), each with a dark and a light variant and the taOS wordmark in the centre. Every file is a JPEG at quality 90 named `<variant>-<WxH>.jpg` under `/usr/share/taos/wallpapers/<id>/`, in three sizes: 3840x2160 (desktop), 3840x1200 (ultrawide) and 1080x2400 (phone, portrait). `/usr/share/taos/wallpapers/index.json` lists id, title, variants and files, and names the default.

    taos-wallpaper list                                        # ids, the default is marked
    taos-wallpaper path                                        # default wallpaper, dark, 3840x2160
    taos-wallpaper path --variant light --size 1080x2400 mesh

`taos-wallpaper path` picks the file closest to the requested size (same orientation first, then nearest aspect ratio, then the smallest file that is large enough) and prints its path. It does not talk to the compositor. The originals are WebGL2 shaders (one GLSL fragment shader each, plus a wordmark signed distance field generated from text at start-up), which no compositor wallpaper tool can run, so the package ships the rendered rasters only.

## Desktop session

`taos-desktop-session` is the graphical login and the taOS Wayland session. How a login runs:

1. greetd starts `/usr/lib/taos/desktop-session/greeter` as the `_greetd` user on VT 7. The wrapper runs `cage -s -- gtkgreet` with `/etc/taos/greetd/gtkgreet.css`. gtkgreet is interim: it is to be replaced by the native taOS greeter.
2. gtkgreet lists `/etc/greetd/environments` (one line, `taos-desktop-session`). After a password greetd starts `/usr/bin/taos-desktop-session` as the user (`/usr/share/wayland-sessions/taos.desktop` lists it for other greeters).
3. The session sets `XDG_CURRENT_DESKTOP=taOS`, `XDG_SESSION_TYPE=wayland`, `MOZ_ENABLE_WAYLAND=1`, `QT_QPA_PLATFORM=wayland`, adds `~/.local/bin` to `PATH` and runs `sway --config /etc/taos/sway/config`. Output goes to the journal (`journalctl -t taos-desktop-session`). If sway exits non-zero within 5 seconds the session logs it and exits non-zero, so greetd shows the login again.
4. sway runs `/usr/lib/taos/desktop-session/autostart` last. That script is the ONE entry point for the graphical shell: it runs `taos-shell-session start` when that command exists, otherwise logs "taOS shell not installed; bare session". The native shell package only has to provide `taos-shell-session`.

VT 7 carries the graphical login. VT 1 to 6 stay text consoles: Ctrl+Alt+F1..F6 always works (the greeter's cage runs with `-s`, and sway handles VT switching), and Ctrl+Alt+F7 returns. The login screen uses a systemd drop-in (`greetd.service.d/10-taos.conf`, `greetd --config /etc/taos/greetd/config.toml`) instead of replacing greetd's own `/etc/greetd/config.toml`, so no dpkg diversion is needed. The drop-in is linked in only by `taos-desktop enable`.

Display GPU: both the greeter and the session export `WLR_DRM_DEVICES` as the resolved target of `/dev/dri/taos-display` (the symlink taos-gpu keeps for the chosen display GPU), so a compute-only NVIDIA card is never opened for display. `--unsupported-gpu` is never passed. If the symlink is missing the compositor picks its own GPU and the journal says so.

Config layering: `/etc/taos/sway/config` is a conffile; do not edit it. Packages add `/etc/taos/sway.d/NN-name.conf` (included in name order) and users add `~/.config/taos/sway.d/*.conf`. The package generates `/etc/taos/sway.d/10-keyboard.conf` once from `/etc/default/keyboard` (sway does not read that file) and removes it on purge. The wallpaper comes from `taos-wallpaper path --variant dark` per active output; the helper tracks its swaybg pids in `$XDG_RUNTIME_DIR/taos-swaybg.pids`. There is no idle or lock rule: a later power package owns that.

    sudo taos-desktop enable     # greetd on, default target graphical (does not start it)
    sudo taos-desktop disable    # default target multi-user, greetd off (a running session stays)
    taos-desktop status          # enabled, VT, display GPU node, shell entry point

Installing the package never starts greetd, never changes the default target and does not link the taOS login config in. Caveat: Debian's own greetd package enables its stock unit on first install (Debian's default target is graphical), so at the next boot stock greetd, a text login on VT 7 and not the taOS screen, can start. If that is unwanted run `sudo taos-desktop disable` right after installing. `taos-desktop status` shows `login screen: not active` until `enable`.

The older `os-build/` tree (Armbian per-board images) predates this and is
not used by the desktop distro.
