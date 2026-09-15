### Fixed

- Auto-install Incus on Alpine (apk) and Arch (pacman) when package manager can resolve it
- Install incus and incus-client always on Alpine, incus-openrc only when OpenRC is present
- Probe resolvability before attempting install on pacman (Arch) to avoid manual-install fallback
- Check for incus unit before starting incusd based on running init system (systemd/OpenRC)
- Add warning for Alpine+systemd case when package installed but no unit exists for running init
- Skip `incus admin init --auto` when incusd could not be started
- Update comment to reflect actual auto-invoke behavior for Arch/Alpine

Changelog fragment added for task tsk-qvdomq.
