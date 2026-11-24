### Fixed

- Installer now installs `gnupg`, `lsb-release`, and `pciutils` as explicit dependencies on all Linux distro paths (apt, dnf, pacman, apk) before steps that require them (NodeSource, Zabbly Incus, Docker official repo). GPG key verification failures now produce clear error messages via `die` instead of silent exits under `set -e`.