import { useState, useEffect } from "react";

// THE VIEWPORT WINDOWS SHOULD BE LAID OUT AGAINST.
//
// The desktop had 27 raw `window.innerWidth` / `window.innerHeight` reads. Once
// a root-level CSS `zoom` exists those stop describing the space windows are
// actually laid out in, so window clamping, maximize and snap maths drift at
// any scale other than 1.0.
//
// WHY clientWidth AND NOT innerWidth / scale.
// MEASURED in Chromium at a 1440x900 viewport, 2026-08-06:
//
//   root zoom | window.innerWidth | documentElement.clientWidth
//   ----------|-------------------|----------------------------
//    1.0      | 1440              | 1440
//    0.8      | 1440  (unchanged) | 1800   (= 1440 / 0.8)
//    1.25     | 1440  (unchanged) | 1152   (= 1440 / 1.25)
//
// `window.innerWidth` is the layout viewport and does NOT move with a root
// zoom. `documentElement.clientWidth` is the root's own content box and tracks
// the zoomed space exactly, which is the space a window's left/top/width live
// in. So clientWidth is the correct read, and it is a measurement rather than
// an inference. Dividing innerWidth by the scale would give the same number
// here but would encode an assumption about the engine instead of asking it.
//
// DELIBERATELY NOT USED FOR DEVICE MODE. `use-device-mode.ts` must keep reading
// the TRUE viewport: display scale is an appearance preference and device mode
// is a form-factor fact. Scaling the UI down to fit more on a laptop must never
// flip the shell into its phone layout. See the test that locks this.

export interface EffectiveViewport {
  width: number;
  height: number;
}

export function readEffectiveViewport(): EffectiveViewport {
  if (typeof document === "undefined") {
    return { width: 0, height: 0 };
  }
  const root = document.documentElement;
  // clientWidth is 0 in some non-rendering test environments; fall back so
  // callers never get a zero-sized desktop and clamp every window to nothing.
  const width = root.clientWidth || window.innerWidth;
  const height = root.clientHeight || window.innerHeight;
  return { width, height };
}

/**
 * Viewport size in the coordinate space windows are laid out in.
 *
 * Updates on resize and whenever the display scale changes, since changing the
 * scale changes the effective viewport without firing a resize event.
 */
export function useEffectiveViewport(): EffectiveViewport {
  const [viewport, setViewport] = useState<EffectiveViewport>(readEffectiveViewport);

  useEffect(() => {
    const update = () => setViewport(readEffectiveViewport());
    window.addEventListener("resize", update);

    // A scale change mutates the root's inline style; no resize event fires, so
    // without this the desktop keeps the pre-scale bounds until the next resize.
    let observer: MutationObserver | undefined;
    if (typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(update);
      observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["style"],
      });
    }

    return () => {
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, []);

  return viewport;
}
