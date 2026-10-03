### Fixed

- The desktop SPA applies the saved reduce-effects preference before first paint
  again. The pre-paint snippet was inline, and the app's Content-Security-Policy
  (`script-src 'self'`) blocks inline scripts, so the browser silently refused to
  run it: users who chose "reduce effects" saw the full effects flash on load
  until React mounted. The snippet now ships as an external same-origin script
  (`desktop/public/boot.js`) and the CSP is unchanged.
- That pre-paint script is precached by the service worker, so an offline PWA
  launch still applies the saved preference instead of flashing the full
  effects, and an online launch no longer waits on a network round-trip for it
  on the critical path.