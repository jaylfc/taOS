import { TEMPLATES } from "./templates";
import type { Template } from "./types";

const HINTS: Record<string, string[]> = {
  "platformer-lite": ["platform", "jump", "run", "runner", "climb", "gravity", "3d"],
  "top-down-collector": ["top-down", "top down", "collect", "collector", "gem", "arena", "dodge", "2d"],
  breakout: ["breakout", "arkanoid", "brick", "paddle", "ball", "puzzle"],
  "orbit-shooter": ["shooter", "shoot", "turret", "orbit", "enemy", "enemies", "space", "blaster"],
};

function scoreTemplate(template: Template, text: string): number {
  let score = 0;
  if (text.includes(template.genre.toLowerCase())) score += 4;
  if (text.includes(template.title.toLowerCase())) score += 3;
  for (const idPart of template.id.split("-")) {
    if (idPart.length > 3 && text.includes(idPart)) score += 2;
  }
  for (const word of template.desc.toLowerCase().split(/\W+/)) {
    if (word.length > 4 && text.includes(word)) score += 1;
  }
  for (const hint of HINTS[template.id] ?? []) {
    if (text.includes(hint)) score += 3;
  }
  return score;
}

/** Pick the closest starter template for a free-text prompt. */
export function matchTemplate(prompt: string): Template {
  const text = prompt.toLowerCase().trim();
  if (!text) return TEMPLATES[0]!;

  let best = TEMPLATES[0]!;
  let bestScore = -1;
  for (const template of TEMPLATES) {
    const score = scoreTemplate(template, text);
    if (score > bestScore) {
      bestScore = score;
      best = template;
    }
  }
  return best;
}
