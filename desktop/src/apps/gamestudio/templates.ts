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
  {
    id: "endless-runner",
    title: "Endless Runner",
    genre: "Runner",
    desc: "Jump over obstacles as the pace keeps climbing in an endless 2D side-scroller. Real canvas physics, no libraries.",
    cover:
      "radial-gradient(120% 120% at 35% 25%, #6a2fa0, transparent 60%), linear-gradient(140deg,#1c1430,#120c1f)",
    files: ["index.html", "game.js"],
  },
  {
    id: "neon-snake",
    title: "Neon Snake",
    genre: "Snake",
    desc: "Classic grid snake with neon styling. Steer with arrows or a swipe, eat food to grow, don't hit yourself or the wall.",
    cover:
      "radial-gradient(120% 120% at 55% 25%, #1f8a4a, transparent 60%), linear-gradient(140deg,#0d1f14,#05100a)",
    files: ["index.html", "game.js"],
  },
  {
    id: "sky-tapper",
    title: "Sky Tapper",
    genre: "Tapper",
    desc: "Flap through the gap in every pipe. One tap or key at a time, real gravity, pixel-precise collision.",
    cover:
      "radial-gradient(120% 120% at 45% 25%, #1f6a8a, transparent 60%), linear-gradient(140deg,#0e2233,#081420)",
    files: ["index.html", "game.js"],
  },
  {
    id: "asteroid-miner",
    title: "Asteroid Miner",
    genre: "Space",
    desc: "Rotate, thrust and blast drifting asteroids in a wraparound 2D arena. Keyboard or on-screen touch controls.",
    cover:
      "radial-gradient(120% 120% at 40% 25%, #5a2a6a, transparent 60%), linear-gradient(140deg,#1c1226,#100a17)",
    files: ["index.html", "game.js"],
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
