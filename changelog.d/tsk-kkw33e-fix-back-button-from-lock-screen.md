### Fixed

The back button from home was taking users to the lock screen instead of returning to the desktop. This was caused by `window.location.assign()` calls in PIN and swipe unlock handlers leaving `/auth/login` in session history.

**Changes:**
- Changed `window.location.assign()` to `window.location.replace()` in PIN panel success handler (auth.py:2605)
- Changed `window.location.assign()` to `window.location.replace()` in swipe unlock success handler (auth.py:6654)
- Added `pageshow` event listener to lock screen script to handle back/forward navigation properly:
  - On back/forward navigation with `/auth/me` 200, redirect to the next URL
  - On back/forward navigation with `/auth/me` 401, stay on lock screen (signed out)
  - Never redirect on normal page load