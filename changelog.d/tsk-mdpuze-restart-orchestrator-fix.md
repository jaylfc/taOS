### Fixed

- Controller restart no longer marks every agent paused forever when its
  framework has no `/prepare-for-shutdown` or `/resume` (Hermes agents).
  Agents are only marked paused when `/prepare-for-shutdown` returns 200,
  `/resume` is only posted to agents that were actually paused, and the
  retry window now clears the paused flag with a notification instead of
  leaving a stuck flag behind.
