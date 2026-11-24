### Fixed

- `taos-kiosk-keyboard`: serialize starts with `flock` on a file descriptor
  (`taos-keyboard.flock`, opened on fd 9, closed before `exec`) instead of a
  symlink lock whose dead owner is reclaimed by check-then-remove. A process
  that dies inside the locked section releases the flock automatically, so
  there is no stale state and nothing to reclaim. Systems without `flock`
  keep the existing symlink lock as an unchanged fallback path.
