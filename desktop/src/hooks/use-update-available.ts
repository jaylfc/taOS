/**
 * Shared hook for detecting when a backend update is available.
 *
 * Compares the SPA build version (__TAOS_VERSION__, injected by Vite) with the
 * backend version reported by the health poll (via BackendStatusContext).
 *
 * In production the build version comes from the __TAOS_VERSION__ global.
 * Pass an explicit buildVersion to override (used by UpdateAvailableToast
 * whose prop already carries the version).
 *
 * Returns true when a newer backend version is available. Both the SPA
 * build version and the backend version are checked against the dev
 * pattern — a dev-style version on either side suppresses the badge
 * so local hacking doesn't trigger false positives.
 */
import { useBackendStatus } from "@/contexts/BackendStatusContext";

declare const __TAOS_VERSION__: string | undefined;

const DEV_VERSION_PATTERN = /^(dev|0\.0\.0)/i;

/** Strip semver build metadata so a new SPA build (e.g. 0.1.0+a3bd632)
 *  against the same backend version (0.1.0) doesn't trigger a spurious
 *  "update available" indicator. */
function strippedVersion(v: string): string {
  const plus = v.indexOf("+");
  return plus === -1 ? v : v.slice(0, plus);
}

/**
 * Returns whether a newer backend version is available than what this SPA
 * was built against. Only meaningful in production builds — dev builds
 * always return false.
 *
 * @param buildVersionOverride — if provided, used instead of the
 *   __TAOS_VERSION__ global (needed when the caller already has the
 *   version as a prop, e.g. UpdateAvailableToast).
 */
export function useUpdateAvailable(buildVersionOverride?: string): boolean {
  const { currentVersion } = useBackendStatus();
  const buildVersion =
    buildVersionOverride ??
    (typeof __TAOS_VERSION__ === "string" ? __TAOS_VERSION__ : "dev");

  if (DEV_VERSION_PATTERN.test(buildVersion)) return false;
  if (!currentVersion || DEV_VERSION_PATTERN.test(currentVersion)) return false;
  return strippedVersion(currentVersion) !== strippedVersion(buildVersion);
}
