import { useEffect, useState } from "react";

/**
 * Returns true when the app is running in standalone/PWA mode.
 * For browser (non-PWA) mode, we need different safe-area and
 * viewport handling to account for Safari's dynamic URL bar and
 * the share/tab bars that consume screen space.
 */
export function useIsPwa(): boolean {
  const [isPwa, setIsPwa] = useState(() => {
    if (typeof window === "undefined") return false;
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      (navigator as unknown as { standalone?: boolean }).standalone === true
    );
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
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
