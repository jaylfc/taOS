// desktop/src/apps/StoreApp/filter.ts
import type { CatalogApp, InstallTarget } from "./types";
import type { Compat, ResolveResponse } from "./resolver-types";

export interface FilterResult {
  compatible: CatalogApp[];
  incompatible: CatalogApp[];
}

/**
 * Filter the catalog by selected devices and backends.
 *
 * Empty arrays mean "no filter" on that axis. Multi-select is union
 * within an axis (any selected device matches; any selected backend
 * matches), intersection across axes (must satisfy both).
 *
 * A model passes the device filter if at least one of its declared
 * hardware_tiers matches one of the selected devices' tier_ids and
 * is not explicitly "unsupported". Models with no hardware_tiers are
 * treated as universally compatible.
 *
 * A model passes the backend filter if any of its variants advertise
 * one of the selected backends. Falls back to install_method when
 * variants[].backend is empty. Models with no declared backend pass
 * the backend filter only when no device filter is active; universal
 * device compatibility already includes them in that case.
 */
export function filterModels(
  apps: CatalogApp[],
  selectedDevices: InstallTarget[],
  selectedBackends: string[],
): FilterResult {
  const tiers = new Set(
    selectedDevices.map((d) => d.tier_id).filter((t): t is string => Boolean(t))
  );
  const requireDeviceMatch = selectedDevices.length > 0;
  const backends = new Set(selectedBackends);

  const compatible: CatalogApp[] = [];
  const incompatible: CatalogApp[] = [];

  for (const app of apps) {
    const deviceOk = !requireDeviceMatch || appMatchesAnyTier(app, tiers);
    const backendOk = backends.size === 0 || appMatchesAnyBackend(app, backends, requireDeviceMatch);

    if (deviceOk && backendOk) compatible.push(app);
    else incompatible.push(app);
  }

  return { compatible, incompatible };
}

/**
 * Decide whether a model card should be shown given the resolver's
 * green/amber/red classification.
 *
 * - `green` and `amber` are always shown — the user's cluster can run
 *   the model (with or without acceleration).
 * - `red` is hidden by default but shown when the IncompatibleToggle is on.
 * - When the resolver hasn't classified the manifest yet (no entry in
 *   `compatMap`), default to showing it — incompatibility is an explicit
 *   negative signal, not a default.
 */
export function compatFromResolver(
  manifestId: string,
  compatMap: Map<string, Compat | ResolveResponse>,
  showIncompatible: boolean,
): boolean {
  const entry = compatMap.get(manifestId);
  if (entry === undefined) return true;
  const c = typeof entry === "string" ? entry : entry.compat;
  if (c === "red") return showIncompatible;
  return true;
}

function appMatchesAnyTier(app: CatalogApp, tiers: Set<string>): boolean {
  // Universal compat: no declared tiers means runs anywhere.
  if (!app.hardware_tiers || Object.keys(app.hardware_tiers).length === 0) {
    return true;
  }
  for (const tid of tiers) {
    const entry = app.hardware_tiers[tid];
    if (entry !== undefined && entry !== "unsupported") return true;
  }
  return false;
}

function appMatchesAnyBackend(app: CatalogApp, backends: Set<string>, requireDeviceMatch: boolean): boolean {
  const appBackends = new Set<string>();
  if (app.variants && app.variants.length > 0) {
    for (const v of app.variants) {
      for (const b of v.backend ?? []) appBackends.add(b);
    }
  }
  // Fallback to install_method when variants don't declare backends.
  if (appBackends.size === 0 && app.install_method) {
    appBackends.add(app.install_method);
  }
  // No backend constraint at all → passes only when no device filter is active.
  if (appBackends.size === 0) return !requireDeviceMatch;

  for (const b of backends) if (appBackends.has(b)) return true;
  return false;
}
