export interface ThemeConfig {
  tokens: Record<string, string>;
  structure: Record<string, { variant?: string } & Record<string, unknown>>;
  effects: { module: string; params?: Record<string, unknown> }[];
  requires: string[];
  wallpaper?: string | null;
}

// Client-side allowlist — mirrors tinyagentos/themes/schema.py _ALL_TOKENS.
export const ALLOWED_TOKENS = new Set<string>([
  "--color-shell-bg","--color-shell-bg-deep","--color-shell-surface",
  "--color-shell-surface-hover","--color-shell-surface-active",
  "--color-shell-border","--color-shell-border-strong",
  "--color-shell-text","--color-shell-text-secondary","--color-shell-text-tertiary",
  "--color-traffic-close","--color-traffic-minimize","--color-traffic-maximize",
  "--color-accent","--color-accent-glow",
  "--color-dock-bg","--color-dock-border","--color-topbar-bg",
  "--color-snap-preview","--color-snap-border",
  "--spacing-topbar-h","--spacing-dock-h","--spacing-dock-padding",
  "--spacing-window-radius","--spacing-dock-radius",
  "--shadow-window","--shadow-window-unfocused","--shadow-dock","--shadow-card","--shadow-card-hover",
  "--font-ui","--font-mono",
]);
