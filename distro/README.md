# taOS desktop distro (Debian 13 trixie base)

One setup script is the source of truth: `sudo ./distro/setup.sh` turns a
minimal Debian 13 install into taOS, and the amd64 ISO will run the same
script. It is idempotent, so re-run it after every pull.

Packages live under `packages/<name>/` as plain dpkg-deb trees;
`build-debs.sh` builds them into `dist/` (gitignored).

| Step | Package | What it does |
|------|---------|--------------|
| 1 | taos-branding | os-release NAME/PRETTY_NAME = taOS (ID stays `debian`, VARIANT_ID=taos), console banner, motd, GRUB entry. Debian's files are diverted, regenerated on base-files updates, restored on removal. |

The older `os-build/` tree (Armbian per-board images) predates this and is
not used by the desktop distro.
