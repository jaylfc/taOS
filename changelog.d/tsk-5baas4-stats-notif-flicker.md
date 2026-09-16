### Fixed

- Phone lock screen: the system stats widget no longer flickers. The stats poll
  runs every three seconds and rebuilt the whole panel each time, and
  `.ls-stat-card` carries the same 520ms entrance animation the agent islands
  do -- so the card replayed its entrance twenty times a minute. Measured in
  chromium at the device's 540x1200 viewport before the fix: two entrance
  replays in 7.5 seconds, the card a different and already-detached element
  each time; after: none. The panel is now reconciled part by part and the
  readings are written in place.

- Phone lock screen: the meter bars now animate to their new value instead of
  snapping to it. `.ls-stat-fill` has a 420ms width transition, which needs an
  element that survives the repaint to have a width to travel FROM; every bar
  was a brand new element, so every bar jumped.

- Phone lock screen: a reading that stops being measured now loses its bar
  rather than leaving the last one on screen. A meter frozen at its final value
  is indistinguishable from a live one.

- Phone lock screen: the notification stacks are reconciled by source, so a
  stack whose notifications have not changed keeps its element, its entrance
  animation and its keyboard focus. A stack whose contents genuinely changed is
  still rebuilt, because that is new content arriving.
