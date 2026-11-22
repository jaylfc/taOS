### Fixed
- Installer drops pciutils and lsb-release from the ensure_linux_deps package lists on apt, dnf, pacman and apk (out of scope for the gnupg card; gnupg/gnupg2 is still installed before the NodeSource step), and tests/test_install_gnupg_missing.py now proves the silent-exit bug against the pre-PR installer instead of masking it with `ensure_node22 && echo || echo`.
