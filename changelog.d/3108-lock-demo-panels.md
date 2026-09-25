- Lock screen: the Phone, Mailbox, Apps and Projects panels now carry content,
  and pending decisions appear at the top of Alerts where they can be approved
  or dismissed without leaving the screen.
- Lock screen: the Settings tab is replaced by Projects.
- Lock screen: panel content is scripted demo data served by
  `/auth/lock-panels`, which is console-only and 404s unless both
  `TAOS_LOCK_DEMO_AGENTS` and `TAOS_LOCK_DEMO_PANELS` are set. The lock screen
  renders before sign-in, so there is no path from any of it to a real account.
