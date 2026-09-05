### Fixed

- Installer wait loops now cap curl probe time and the follow-up sleep by the remaining phase deadline, preventing a stuck probe from pushing the port-open or readiness phase past `_PORT_WAIT` / `_READY_WAIT`.
