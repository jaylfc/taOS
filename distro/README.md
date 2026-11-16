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

## Display GPU

Policy `auto` picks an integrated GPU with a connected display, else any integrated GPU, else the boot_vga GPU, else the first GPU. A discrete NVIDIA card is never picked while an integrated GPU exists. NVIDIA cards are compute only: nouveau is blacklisted and nvidia-drm runs with modeset=0 (the proprietary driver still loads for CUDA).

    taos-gpu list [--json]       # GPUs, kind, connectors, display marker
    taos-gpu status [--json]     # policy, display GPU, compute-only GPUs
    sudo taos-gpu set-display auto|0000:00:02.0

Changes need a reboot. The future control panel reads `taos-gpu list --json` and calls `taos-gpu set-display`. The sway session sources `/etc/taos/session.env.d/` (`WLR_DRM_DEVICES=/dev/dri/taos-display`, a stable symlink made by a udev rule).

The older `os-build/` tree (Armbian per-board images) predates this and is
not used by the desktop distro.
