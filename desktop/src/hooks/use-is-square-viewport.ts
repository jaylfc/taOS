import { useEffect, useState } from "react";

/**
 * True when the viewport is roughly square or wider — height is no more than
 * `1 / minRatio` times the width.
 *
 * Ordinary phones are tall and narrow (9:19.5 is a width/height ratio of about
 * 0.46), so they never match. Square-screen devices like the Unihertz Titan 2
 * sit at about 1.0 and always do. The default threshold of 3/4 (0.75) leaves a
 * wide margin either side of both cases, so a device has to be genuinely
 * square-ish to match rather than merely short.
 *
 * Vertical space is scarce on these screens, so any fixed-pixel reserve costs a
 * visibly larger slice of the display than it does on a tall phone. Callers use
 * this to shrink such reserves instead of paying a tall-phone tax on a screen
 * that is not tall.
 *
 * Note this is deliberately NOT `(orientation: portrait)`: that matches every
 * screen where height >= width, which includes square displays, and doing so
 * already caused a wallpaper bug on exactly this class of device (see the
 * comment on the wallpaper media query in theme/tokens.css).
 */
/**
 * `minRatio` is a CSS ratio written as a fraction, e.g. "3/4". It is passed
 * through verbatim rather than as a decimal: a single-number ratio is only
 * Media Queries Level 4 and does not parse on older Safari, where the query
 * would silently never match. The fraction form is understood everywhere.
 * Should it ever fail to parse, the hook returns false and the caller keeps
 * its previous behaviour, so the failure mode is "no change", not a broken
 * layout.
 */
export function useIsSquareViewport(minRatio = "3/4"): boolean {
  const query = `(min-aspect-ratio: ${minRatio})`;

  const [isSquare, setIsSquare] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(query);
    const update = () => setIsSquare(mql.matches);
    update();
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, [query]);

  return isSquare;
}
