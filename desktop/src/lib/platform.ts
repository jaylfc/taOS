/** Shared platform / install-state detection for the install UI. */

/** True on iPhone, iPod, and iPadOS (which reports as MacIntel with touch). */
export function isIOS(): boolean {
  return (
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/** True when the app is running as an installed PWA (home-screen / standalone). */
// "Already an app": an installed PWA (standalone), or a browser the device
// itself runs fullscreen -- taOSmobile's kiosk is chromium --kiosk, which
// reports display-mode: fullscreen, and offering to install taOS on the
// phone that IS taOS was the banner Jay saw after signing in.
export function isStandalone(): boolean {
  return (
    (window.navigator as unknown as { standalone?: boolean }).standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches ||
    window.matchMedia("(display-mode: fullscreen)").matches
  );
}
