### Fixed
- Hard disk quota on a restart-paused agent now claims the pause (clears `paused_by_restart`), preventing the restart resume path from unpausing an agent whose disk is full.