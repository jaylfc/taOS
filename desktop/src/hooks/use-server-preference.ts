import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Cross-device preference hook.
 *
 * Stores a JSON-serialisable value under a namespaced key on the server so
 * the same user sees the same setting on every device they sign into. A
 * localStorage mirror keeps the value available instantly on boot before
 * the fetch completes, so there's no flash of default content.
 *
 * Usage:
 *   const [home, setHome] = useServerPreference<Location | null>(
 *     "weather",
 *     null,
 *     (v) => v?.home ?? null,
 *     (home) => ({ home }),
 *   );
 *
 * ``serverKey`` is the namespace used in ``/api/preferences/{key}``. Every
 * call to ``setValue`` writes to localStorage immediately and schedules a
 * debounced PUT to the server (500ms).
 */
export function useServerPreference<T>(
  serverKey: string,
  defaultValue: T,
  // Optional projectors let the caller extract a sub-field from the server
  // blob instead of using the whole blob as the value. Useful when several
  // hooks share a namespace.
  extract: (blob: Record<string, unknown>) => T = (blob) => blob as unknown as T,
  pack: (value: T) => Record<string, unknown> = (value) => ({ value: value as unknown }),
): [T, (value: T | ((prev: T) => T)) => void, { loaded: boolean }] {
  const localCacheKey = `taos-pref:${serverKey}`;
  const [value, setValueState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(localCacheKey);
      if (raw !== null) return JSON.parse(raw) as T;
    } catch {
      // ignore parse errors
    }
    return defaultValue;
  });
  const [loaded, setLoaded] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch the server value on mount and hydrate local state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`/api/preferences/${encodeURIComponent(serverKey)}`);
        if (!resp.ok) {
          setLoaded(true);
          return;
        }
        const blob = (await resp.json()) as Record<string, unknown>;
        if (cancelled) return;
        // Empty server response means "never saved" — keep current value.
        if (blob && typeof blob === "object" && Object.keys(blob).length > 0) {
          const next = extract(blob);
          setValueState(next);
          try {
            localStorage.setItem(localCacheKey, JSON.stringify(next));
          } catch {
            // quota or disabled storage — not fatal
          }
        }
        setLoaded(true);
      } catch {
        setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey]);

  const setValue = useCallback(
    (updater: T | ((prev: T) => T)) => {
      setValueState((prev) => {
        const next =
          typeof updater === "function"
            ? (updater as (p: T) => T)(prev)
            : updater;
        try {
          localStorage.setItem(localCacheKey, JSON.stringify(next));
        } catch {
          // ignore
        }
        // Debounce server writes so rapid changes (e.g. dragging a slider)
        // don't spam the backend.
        if (saveTimer.current !== null) {
          clearTimeout(saveTimer.current);
        }
        saveTimer.current = setTimeout(() => {
          saveTimer.current = null;
          fetch(`/api/preferences/${encodeURIComponent(serverKey)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(pack(next)),
          }).catch(() => {
            // network error — local cache still has the value, will
            // re-try on the next change
          });
        }, 500);
        return next;
      });
    },
    [localCacheKey, pack, serverKey],
  );

  return [value, setValue, { loaded }];
}
