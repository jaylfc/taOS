import { useEffect, useRef } from "react";

const DEFAULT_DELAY = 1000;

/**
 * Re-runs `refetch` when the window regains focus or the document becomes
 * visible again, coalescing bursts within `delayMs`.
 *
 * `refetch` is invoked with NO ARGUMENTS. TypeScript accepts a function whose
 * parameters are all optional or defaulted, so passing one directly compiles
 * and then silently refetches the default — `fetchFiles(path = "")` reloaded
 * the workspace root over whatever directory the user was in. Wrap anything
 * that takes arguments: `useCallback(() => fetchFiles(currentPath), [...])`.
 */
export function useRefreshOnFocus(
  refetch: () => void | Promise<void>,
  delayMs = DEFAULT_DELAY,
) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refetchRef = useRef(refetch);

  refetchRef.current = refetch;

  useEffect(() => {
    if (typeof window === "undefined") return;

    const clearTimer = () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const schedule = () => {
      clearTimer();
      timerRef.current = setTimeout(async () => {
        timerRef.current = null;
        try {
          await refetchRef.current();
        } catch {
          // swallow errors from background refetches
        }
      }, delayMs);
    };

    const onFocus = () => schedule();
    const onVisibilityChange = () => {
      if (!document.hidden) schedule();
    };

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      clearTimer();
    };
  }, [delayMs]);
}
