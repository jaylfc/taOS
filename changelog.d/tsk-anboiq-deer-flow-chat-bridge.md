### Fixed

- A deployed DeerFlow agent can now receive chat: `tinyagentos/scripts/install_deer-flow.sh` provisions DeerFlow and installs a taOS SSE bridge that forwards each message to DeerFlow's `POST /api/runs/wait` and posts the reply back, and `deer-flow` is registered in `FRAMEWORKS`. Previously the deployer ran the catalog `install.sh`, which wrote no bridge, so messages never reached the agent.
