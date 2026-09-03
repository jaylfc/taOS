### Fixed
- Zabbly keyring written 0600 breaks every subsequent apt-get update: changed `cp` to `install -m 0644` in scripts/install-server.sh
