### Fixed
- Installer now installs `gnupg` (providing `gpg`) as a dependency on all Linux distro paths (apt, dnf, pacman, apk) before the NodeSource step, preventing silent exits on fresh Debian systems where `gpg` was missing. Added safety checks in `ensure_node22`, Zabbly Incus fallback, and Docker official-repo fallback to die/warn with a clear message if `gpg` is unavailable.
