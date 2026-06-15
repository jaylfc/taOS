/**
 * Presentation metadata for the optional, Store-installable frontend apps
 * (Reddit / YouTube / GitHub / X). The registry (app-registry.ts) owns the
 * runtime manifest (component, sizing); this owns how they look in the Store's
 * "taOS Apps" section. ids must match the registry entries marked `optional`.
 */

export interface OptionalAppMeta {
  /** Registry app id — must match an `optional: true` manifest. */
  id: string;
  /** Display name. */
  name: string;
  /** lucide-react icon name (rendered via the shared Icon component). */
  icon: string;
  /** One-line description for the Store card. */
  tagline: string;
  /** Brand-tinted CSS background for the card cover. */
  cover: string;
}

export const OPTIONAL_APPS: OptionalAppMeta[] = [
  {
    id: "reddit",
    name: "Reddit",
    icon: "scroll-text",
    tagline: "Browse subreddits, posts, and comments inside taOS.",
    cover: "linear-gradient(135deg, #ff4500 0%, #cc3700 100%)",
  },
  {
    id: "youtube-library",
    name: "YouTube",
    icon: "play-circle",
    tagline: "A focused video library and watch surface.",
    cover: "linear-gradient(135deg, #ff0033 0%, #b3001f 100%)",
  },
  {
    id: "github-browser",
    name: "GitHub",
    icon: "github",
    tagline: "Track repos, issues, and pull requests at a glance.",
    cover: "linear-gradient(135deg, #2b3137 0%, #14161a 100%)",
  },
  {
    id: "x-monitor",
    name: "X",
    icon: "at-sign",
    tagline: "Monitor timelines and posts from X.",
    cover: "linear-gradient(135deg, #1a1a1a 0%, #000000 100%)",
  },
];
