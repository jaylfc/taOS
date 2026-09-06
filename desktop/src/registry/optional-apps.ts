/**
 * Presentation metadata for optional, Store-installable frontend apps. The
 * registry (app-registry.ts) owns the runtime manifest (component, sizing);
 * this owns how they look in the Store's "taOS Apps" section. ids must match
 * the registry entries marked `optional`.
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
    tagline: "Browse communities and threads, AI-summarised",
    cover: "radial-gradient(120% 120% at 35% 25%,#4a2a1a,transparent 60%),linear-gradient(140deg,#261408,#180d05)",
  },
  {
    id: "youtube-library",
    name: "YouTube",
    icon: "play-circle",
    tagline: "Watch and organise your video library",
    cover: "radial-gradient(120% 120% at 65% 25%,#4a1a1a,transparent 60%),linear-gradient(140deg,#261008,#180805)",
  },
  {
    id: "github-browser",
    name: "GitHub",
    icon: "github",
    tagline: "Browse repos, issues, and PRs from your desktop",
    cover: "radial-gradient(120% 120% at 30% 20%,#1a1a2e,transparent 60%),linear-gradient(140deg,#0d0d1a,#080810)",
  },
  {
    id: "x-monitor",
    name: "X",
    icon: "at-sign",
    tagline: "Follow topics and conversations in real time",
    cover: "radial-gradient(120% 120% at 70% 20%,#1a1a1a,transparent 60%),linear-gradient(140deg,#111111,#0a0a0a)",
  },
];
