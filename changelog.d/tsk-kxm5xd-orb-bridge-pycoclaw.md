### Added

- Orb bridge adapter for PycoClaw (`tinyagentos/orb/bridges/pycoclaw.py`): PURE adapter that surfaces `webrepl` URLs (`https://{host}/webrepl`) for orbs whose `app` is `pycoclaw`, discovered via `orbs_running`/`orb_host`/`target`, with top-level instruction "No Orb running PycoClaw found on the LAN." when there are no targets
