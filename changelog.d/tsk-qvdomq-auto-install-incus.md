### Fixed

- Auto-install Incus on Alpine (apk) and Arch (pacman) when package is available in repos
- Check init system present (systemd vs OpenRC) and start incusd appropriately
- Handle Alpine+systemd edge case where package installs but no init unit exists
- Fixed comment about AUR/apk claim (Incus is in official Arch "extra" repo)
  
  scripts/install-server.sh:822-947, 955-1005