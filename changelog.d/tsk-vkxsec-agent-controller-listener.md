### Fixed

- Agent containers now reach the controller through a dedicated loopback listener (`agent_controller_port`, default 6972) so agent requests are never mistaken for the local console. New deploys are refused until the listener is verified, and existing agents are moved onto it at controller startup.
