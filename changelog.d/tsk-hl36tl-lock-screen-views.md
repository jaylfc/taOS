### Added

- Phone lock screen: a row of seven icons above the feed chooses what it shows
  — agents, phone, mailbox, apps, alerts, system and settings — with agents the
  default and the resting state. It is a real tablist: one tab stop, traversed
  with the arrow keys, so it does not put six dead ends between the clock and
  the unlock button.
- Phone lock screen: long-pressing an agent island's avatar opens that agent's
  menu. Nothing in it acts on the agent — the screen renders before sign-in, so
  the menu records what was asked for and then requires the passcode, the way
  the decision sheet already does. A short tap and a long press on the rest of
  the island keep opening the conversation as before.
- Phone lock screen: a system view reporting CPU, memory and GPU clock, plus the
  remote processors as running/offline chips, from a new console-only
  `/auth/lock-stats`. It reports only what the hardware exposes: there is no NPU
  utilisation counter on this SoC, so none is shown, the GPU is labelled as a
  clock rather than as usage, and anything unmeasured renders `--` rather than
  zero.

### Changed

- Phone lock screen: the agent islands, notification stacks, view row and stats
  card are ~10% wider (a 396px cap becomes 436px, now a single token rather than
  six copies of the same number). Measured on the handset rather than guessed:
  sway drives the panel at 1080x2400 with scale 2.0, so the page gets a 540px
  CSS viewport and that cap is what was actually holding the cards in — widening
  the side padding instead would only have moved the gutters.
- Phone lock screen: the taOS wordmark and the battery reading sit level with
  the middle of the punch-hole camera instead of above it. The camera's centre
  is a measurement, not a nudge: the panel's own vendor description puts it
  68.50 physical pixels down, which is 34.25 CSS pixels at the scale this
  display runs at.
