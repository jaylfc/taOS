### Fixed
- Built service worker is now a self-contained classic worker (IIFE, no
  top-level import/export), so registration no longer throws a SyntaxError
  and the fast-boot precache populates in every browser.
- Registration failures are now surfaced via console.error and a
  `taos-sw-registration-failed` custom event instead of being silently
  swallowed.
