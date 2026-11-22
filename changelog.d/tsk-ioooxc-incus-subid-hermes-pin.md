### Fixed

- Agent deploys now start on a fresh host: the installer maps the taos service account into Incus by granting root a one-UID subid range in /etc/subuid and /etc/subgid (restarting incus only when a line is added), the deployer fails fast with a clear one-line fix instead of the opaque Instance is not running when the mapping is missing, and the default Hermes pin is bumped to v0.23.2.
