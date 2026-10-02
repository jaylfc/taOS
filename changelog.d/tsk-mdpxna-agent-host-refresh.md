### Fixed

- Agent host records now automatically refresh from Incus when containers move between projects/bridges, preventing controller-to-agent connection failures due to stale IPs. The refresh happens at controller startup and when controller-to-agent calls fail, using the agent's Incus project instead of the default project for lookup.