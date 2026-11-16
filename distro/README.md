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

The older `os-build/` tree (Armbian per-board images) predates this and is
not used by the desktop distro.
