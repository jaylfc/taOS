import { useEffect, useState } from "react";

/**
 * Returns true when the app is running in standalone/PWA mode.
 * For browser (non-PWA) mode, we need different safe-area and
 * viewport handling to account for Safari's dynamic URL bar and
 * the share/tab bars that consume screen space.
 */
function isStandaloneNow(): boolean {
  if (typeof window === "undefined") return false;
  // matchMedia is absent in some embedded webviews / SSR / older runtimes —
  // guard it rather than assume it's callable.
  const standaloneDisplay =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(display-mode: standalone)").matches;
  return (
    standaloneDisplay ||
    (navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

export function useIsPwa(): boolean {
  const [isPwa, setIsPwa] = useState(isStandaloneNow);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia("(display-mode: standalone)");
    const update = () =>
      setIsPwa(
        mql.matches ||
          (navigator as unknown as { standalone?: boolean }).standalone === true
      );
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, []);

  return isPwa;
}
