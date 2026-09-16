### Changed

- Phone lock screen: the notification stacks now have their own switch,
  `TAOS_LOCK_DEMO_NOTIFICATIONS`, and are off by default while they are
  redesigned. It is required on top of `TAOS_LOCK_DEMO_AGENTS` rather than
  replacing it, so the agent islands stay up with no stacks under them, and
  turning off the master flag still takes down everything invented on this
  pre-sign-in screen in one move.
