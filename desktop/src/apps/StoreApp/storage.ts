// desktop/src/apps/StoreApp/storage.ts

interface PersistedFilter {
  devices: string[];
  backends: string[];
}

/** Build the localStorage key for a (user, profile) pair. */
function key(userId: string, profileId: string): string {
  return `taos.store.filter.${userId}.${profileId}`;
}

/**
 * Hydrate a previously-saved filter from localStorage. Names that no
 * longer exist in `validDevices` or `validBackends` are dropped before
 * returning, so a stale filter never references a removed worker.
 */
export function loadFilter(
  userId: string,
  profileId: string,
  validDevices: string[],
  validBackends: string[],
): PersistedFilter {
  if (typeof window === "undefined") return { devices: [], backends: [] };
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(key(userId, profileId));
  } catch {
    return { devices: [], backends: [] };
  }
  if (!raw) return { devices: [], backends: [] };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { devices: [], backends: [] };
  }
  if (!parsed || typeof parsed !== "object") {
    return { devices: [], backends: [] };
  }
  const obj = parsed as { devices?: unknown; backends?: unknown };

  const validDeviceSet = new Set(validDevices);
  const validBackendSet = new Set(validBackends);

  const devices = Array.isArray(obj.devices)
    ? obj.devices.filter(
        (d): d is string => typeof d === "string" && validDeviceSet.has(d)
      )
    : [];
  const backends = Array.isArray(obj.backends)
    ? obj.backends.filter(
        (b): b is string => typeof b === "string" && validBackendSet.has(b)
      )
    : [];

  return { devices, backends };
}

export function saveFilter(
  userId: string,
  profileId: string,
  filter: PersistedFilter,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key(userId, profileId), JSON.stringify(filter));
  } catch {
    // localStorage may be unavailable (private mode, quota); fail silently.
  }
}
