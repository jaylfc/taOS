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

## Display GPU

Policy `auto` picks an integrated GPU with a connected display, else any integrated GPU, else the boot_vga GPU, else the first GPU. A discrete NVIDIA card is never picked while an integrated GPU exists. NVIDIA cards are compute only: nouveau is blacklisted and nvidia-drm runs with modeset=0 (the proprietary driver still loads for CUDA).

    taos-gpu list [--json]       # GPUs, kind, connectors, display marker
    taos-gpu status [--json]     # policy, display GPU, compute-only GPUs
    sudo taos-gpu set-display auto|0000:00:02.0

Changes need a reboot. The future control panel reads `taos-gpu list --json` and calls `taos-gpu set-display`. The sway session sources `/etc/taos/session.env.d/` (`WLR_DRM_DEVICES=/dev/dri/taos-display`, a stable symlink made by a udev rule).

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

The older `os-build/` tree (Armbian per-board images) predates this and is
not used by the desktop distro.
