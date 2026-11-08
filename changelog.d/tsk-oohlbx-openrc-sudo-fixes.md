### Fixed

- `scripts/install-llama-cpp.sh`: `install_openrc_service` no longer calls bare `sudo tee` / `sudo chmod` / `sudo rc-update` / `sudo rc-service`. It now sets `SUDO` locally (`if [ "$(id -u)" -eq 0 ]; then SUDO=; else SUDO=sudo; fi`, mirroring `build_from_source_musl`) and prefixes all four calls with `$SUDO`, so the install no longer dies on postmarketOS/Alpine where sudo is absent and the script runs as root.

- `scripts/install-llama-cpp.sh`: the musl arm64 build copies shared libraries FIRST (a `find ... -print0` loop that `die`s on any failed `cp -P`) and `llama-server` LAST. The previous `find ... -exec cp -P {} \;` swallowed cp failures (find's exit status is unaffected by `-exec`'s child), so a dropped `.so` left an unrunnable server while the script reported success.

- `scripts/install-llama-cpp.sh`: `install_openrc_service` now `die`s if `MODELS_DIR` contains a double quote or a backslash before writing the OpenRC init script, since `MODELS_DIR` is interpolated inside `\"...\"` quotes in `command_args`.