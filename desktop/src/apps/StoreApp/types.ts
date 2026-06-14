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
  /** True when an installed app has a newer version available (drives Updates). */
  update_available?: boolean;
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
