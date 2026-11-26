### Fixed
- A deployed agent's registry entry now records who deployed it, and agents deployed earlier with no recorded owner are given to the install owner at startup. A per-agent token acts for that owner, so without it a new agent could not post to chat.
