import { TEMPLATES, DEFAULT_TEMPLATE, type SiteTemplate } from "./templates";

/**
 * Phase-1 prompt understanding: match a free-text prompt to the closest
 * starter template by keyword hits. This is an honest stand-in for full
 * offline-LLM generation (a later phase); it always returns a real,
 * editable template rather than faking a bespoke build.
 */
export function matchTemplate(prompt: string): SiteTemplate {
  const text = prompt.toLowerCase();
  if (!text.trim()) return DEFAULT_TEMPLATE;

  let best: SiteTemplate = DEFAULT_TEMPLATE;
  let bestScore = 0;

  for (const tpl of TEMPLATES) {
    let score = 0;
    for (const kw of tpl.keywords) {
      if (text.includes(kw)) score += 2;
    }
    if (text.includes(tpl.name.toLowerCase())) score += 3;
    if (score > bestScore) {
      bestScore = score;
      best = tpl;
    }
  }
  return best;
}
