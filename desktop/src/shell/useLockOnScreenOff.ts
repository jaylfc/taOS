import { useEffect } from "react";
import { withCsrf } from "@/lib/csrf";

/**
 * Lock the session when the handset's screen goes off.
 *
 * Jay: "when i lock the phone from inside of taos and turn the phone back on
 * im in taos and not the lockscreen". The power key only blanks the panel; on
 * taOSmobile the kiosk IS this page, so waking showed the desktop again with
 * the session still open.
 *
 * The phone tells its pages the screen is going off over /auth/lock-events,
 * the lock screen's own stream. It is console-only: a remote browser gets a
 * 403, an EventSource treats a non-200 answer as final and closes for good, so
 * this is inert everywhere except on the device itself.
 *
 * Order matters. taos-kiosk-screen waits ~0.55 s after the event before the
 * output powers down, and nothing paints on a dark panel -- so the FIRST thing
 * is a black cover, making the last frame on the panel black instead of the
 * desktop. Then the same lock as the top bar's Lock (revoke the session), and
 * off to the lock screen, which loads while the panel is dark.
 */
export function useLockOnScreenOff(): void {
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let stream: EventSource;
    try {
      stream = new EventSource("/auth/lock-events");
    } catch {
      return;
    }
    let locking = false;
    const onScreenOff = async () => {
      if (locking) return;
      locking = true;
      const cover = document.createElement("div");
      cover.setAttribute("aria-hidden", "true");
      cover.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#000";
      document.body.appendChild(cover);
      try {
        await fetch("/auth/lock", {
          method: "POST",
          credentials: "include",
          headers: withCsrf({ method: "POST" })?.headers,
        });
      } catch {
        // The lock page still asks for a PIN if the session survived.
      }
      window.location.replace("/auth/login");
    };
    stream.addEventListener("screen-off", onScreenOff);
    return () => {
      stream.removeEventListener("screen-off", onScreenOff);
      stream.close();
    };
  }, []);
}
