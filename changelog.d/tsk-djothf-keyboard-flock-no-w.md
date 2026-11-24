### Fixed

- `taos-kiosk-keyboard`: wait for the start lock with a bounded loop of
  non-blocking `flock -n` attempts and a short sleep instead of `flock -w 30`.
  BusyBox `flock` has no `-w` option, so on the handset the old call was
  rejected, the wrapper printed "gave up" and exited 1, and the on-screen
  keyboard never started. The retry loop works on both util-linux and BusyBox;
  it still gives up (clear message, non-zero exit) after a bounded number of
  tries rather than hang, and honours `TAOS_KEYBOARD_LOCK_TRIES`.
