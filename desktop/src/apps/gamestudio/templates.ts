import type { Template } from "./types";

/* ------------------------------------------------------------------ */
/*  Starter templates                                                  */
/*                                                                     */
/*  Each template is a real, playable seed shipped as static files      */
/*  under desktop/public/gamestudio-seeds/{id}/ (served at              */
/*  /desktop/gamestudio-seeds/{id}/{file} -- the desktop app's Vite      */
/*  base). Every seed is fully self-contained: the two 3D templates      */
/*  bundle three.js as a relative three.module.js, never a CDN.         */
/* ------------------------------------------------------------------ */

export const TEMPLATES: Template[] = [
  {
    id: "platformer-lite",
    title: "Platformer Lite",
    genre: "Platformer",
    desc: "Jump across floating platforms and collect every gem in a real 3D scene with gravity and a follow camera.",
    cover:
      "radial-gradient(120% 120% at 30% 20%, #1f5a3a, transparent 60%), linear-gradient(140deg,#12261b,#0c1712)",
    files: ["index.html", "game.js", "three.module.js"],
  },
  {
    id: "top-down-collector",
    title: "Top-Down Collector",
    genre: "Top-down",
    desc: "Move around a bounded arena collecting gems while dodging bouncing hazards. Real canvas 2D movement and collision.",
    cover:
      "radial-gradient(120% 120% at 60% 25%, #2a3f7a, transparent 60%), linear-gradient(140deg,#141a2b,#0d1119)",
    files: ["index.html", "game.js"],
  },
  {
    id: "breakout",
    title: "Breakout",
    genre: "Arcade",
    desc: "Classic paddle-and-ball brick breaker with real physics, lives and a win/lose state.",
    cover:
      "radial-gradient(120% 120% at 60% 25%, #5a3a1f, transparent 60%), linear-gradient(140deg,#231811,#16100a)",
    files: ["index.html", "game.js"],
  },
  {
    id: "orbit-shooter",
    title: "Orbit Shooter",
    genre: "Shooter",
    desc: "Aim your turret with the mouse and shoot down enemies closing in from every side in a 3D arena.",
    cover:
      "radial-gradient(120% 120% at 40% 30%, #16607a, transparent 60%), linear-gradient(140deg,#0e2230,#0a1620)",
    files: ["index.html", "game.js", "three.module.js"],
  },
];

export const DEFAULT_TEMPLATE: Template = TEMPLATES[0]!;

export function findTemplate(id: string): Template | undefined {
  return TEMPLATES.find((t) => t.id === id);
}

/** Fetch a template's seed files as {filename: content}. three.module.js
 *  (when present) is a plain ES module text file, not a binary asset, so it
 *  round-trips through the same text-file storage as every other game file. */
export async function fetchSeedFiles(template: Template): Promise<Record<string, string>> {
  const entries = await Promise.all(
    template.files.map(async (name) => {
      const res = await fetch(`/desktop/gamestudio-seeds/${template.id}/${name}`);
      if (!res.ok) {
        throw new Error(`Failed to load "${name}" for the ${template.title} template (HTTP ${res.status})`);
      }
      return [name, await res.text()] as const;
    }),
  );
  return Object.fromEntries(entries);
}
