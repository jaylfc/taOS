### Fixed

- Store uninstall of the llama-cpp service now tears down its init system service instead of only marking it uninstalled: on systemd it runs `systemctl disable --now llama-cpp.service` and removes `/etc/systemd/system/llama-cpp.service`, and on OpenRC it runs `rc-service llama-cpp stop`, `rc-update del llama-cpp default`, and removes `/etc/init.d/llama-cpp`. The teardown is driven by a new `scripts/uninstall-llama-cpp.sh` declared as `install.uninstall` in the service manifest and is best-effort so a missing unit or init script never fails the uninstall.
