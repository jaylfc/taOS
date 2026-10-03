export interface CatalogApp {
  id: string;
  name: string;
  type: string;
  category?: string;
  version: string;
  description: string;
  installed: boolean;
  compat: "green" | "yellow" | "unsupported";
  install_method?: string;
  hardware_tiers?: Record<string, unknown>;
  variants?: Array<{
    id: string;
    name?: string;
    backend?: string[];
    [key: string]: unknown;
  }>;
  /** GitHub owner/repo slug for star count display (e.g. "home-assistant/core"). */
  repo?: string;
  /** dashboard-icons CDN slug for the official logo image. */
  iconSlug?: string;
  /** Real GitHub star count (e.g. 72400). */
  stars?: number;
  /** Short tagline used in hero and rich-card previews. */
  tagline?: string;
  /** Cover art URL or gradient CSS value for rich cards. */
  cover?: string;
  /**
   * Real cover photo (official screenshot / hero) shown behind a featured
   * or carousel card. A bottom-up dark scrim keeps overlaid text legible.
   * Falls back to `cover` (gradient) when absent or if the image fails to load.
   */
  coverImage?: string;
  /** True when an installed app has a newer version available (drives Updates). */
  update_available?: boolean;
  /**
   * Newest registry tag matching the pinned tag's shape, from the
   * daily cached upstream check of docker-image apps. Null/undefined
   * when the upstream state is unknown (not checked yet, or the check
   * failed) -- unknown is never "no update".
   */
  upstream_version?: string | null;
  /**
   * The docker image tag the upstream check compared against. This --
   * not `version`, the catalog's own revision of the app -- is the
   * baseline, so the badge shows `v<pin> → v<upstream>`.
   */
  upstream_pinned_version?: string | null;
  /** True when upstream_version is newer than the pinned image tag. */
  upstream_update_available?: boolean | null;
  /** Epoch seconds of the last upstream check, when one has happened. */
  upstream_checked_at?: number | null;
  /** Studios-specific lifecycle state. "soon" hides install and shows a badge. */
  studioState?: "installed" | "available" | "soon";
  /** Code license (e.g. "MIT"). Distinct from weights_license/license_class below. */
  license?: string;
  /** Model weights license label (e.g. "CC-BY-NC 4.0"), when the backend pins weights
   * under a different license than the runtime code. */
  weights_license?: string;
  /** "permissive" | "non-commercial" | "" (unknown/code-only). Drives the
   * "Non-commercial weights" badge and the install-time license gate. */
  license_class?: string;
}

export interface InstallTarget {
  name: string;
  label: string;
  type: "local" | "remote";
  addr?: string;
  /** Hardware tier ID matching keys in CatalogApp.hardware_tiers. */
  tier_id?: string;
  /** Display name for pill bars. Defaults to `label` when absent. */
  friendly_name?: string;
  /**
   * False when the remote is an incus remote not yet registered as a taOS
   * cluster worker. When false, tier_id is "unknown" and the filter should
   * treat this device as if no device filter is active (show all).
   */
  hardware_known?: boolean;
}

export interface InstalledEntry {
  app_id: string;
  installed_at: number;
  version: string;
  metadata: Record<string, unknown>;
  runtime_host: string | null;
  runtime_port: number | null;
  runtime_backend: string | null;
}
