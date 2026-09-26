### Added
- Settings -> Lock screen: choose how this device's own screen unlocks -- Swipe,
  PIN or Password (Pattern is listed as coming soon). Changing it needs your
  current password. The console lock screen now appears on any single-account
  install, with or without a PIN.
  Swipe opens taOS through the new `POST /auth/swipe-unlock`, which is honoured
  only from the device's own console (no forwarding headers, no cross-origin
  caller, and, like every lock-screen POST, never from a simple form or no-cors
  request: it needs `X-taOS-Console` or a JSON body), only on a single-account install, only for an owner who chose Swipe,
  and under the same throttle as PIN sign-in. Picking Password now really
  turns PIN entry off: a PIN that still exists is refused.
