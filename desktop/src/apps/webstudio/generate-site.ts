import { streamTaosAgentChat } from "../appstudio/stream-chat";
import { matchTemplate } from "./match-template";
import { siteFromTemplate } from "./templates";
import { buildWebGenerationSystemPrompt } from "./web-authoring-prompt";
import { PALETTES, FONTS, isValidSite, type Site } from "./types";

/* ------------------------------------------------------------------ */
/*  Real AI site generation                                            */
/*                                                                     */
/*  Streams the taos-agent the same way Game Studio's generateGame()    */
/*  does, asking it for a single JSON `Site` document. The response is  */
/*  tolerant-parsed (strips code fences / surrounding prose), then      */
/*  validated with isValidSite() plus a palette/font enum check. On any  */
/*  failure -- empty stream, unparsable JSON, or a shape that doesn't    */
/*  match -- the caller gets the honest, already-working matchTemplate() */
/*  fallback instead, never a fake "generated" result.                   */
/* ------------------------------------------------------------------ */

export type GenerateStage = "streaming" | "parsing" | "done";

export interface GenerateProgress {
  stage: GenerateStage;
  detail: string;
}

export interface GenerateSiteResult {
  site: Site;
  usedFallback: boolean;
  parseNotice: string | null;
}

/** Pull the JSON object out of a model response that may wrap it in a
 *  markdown code fence and/or surrounding prose. Returns null if no
 *  plausible `{ ... }` object can be found. */
export function extractJsonObject(raw: string): string | null {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1]! : raw;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return null;
  return candidate.slice(start, end + 1);
}

function parseSite(raw: string): Site | null {
  const jsonText = extractJsonObject(raw);
  if (!jsonText) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    return null;
  }
  if (!isValidSite(parsed)) return null;
  // isValidSite only checks shape; also require the theme ids the app
  // actually knows how to render, so an unrecognized value from the model
  // never crashes PALETTES[palette]/FONTS[font] lookups downstream.
  if (!(parsed.theme.palette in PALETTES) || !(parsed.theme.font in FONTS)) return null;
  return parsed;
}

function fallbackSite(prompt: string): Site {
  const tpl = matchTemplate(prompt);
  return siteFromTemplate(tpl, prompt.trim() ? prompt.trim().slice(0, 60) : undefined);
}

export async function generateSite(
  prompt: string,
  onProgress: (p: GenerateProgress) => void,
  opts?: { signal?: AbortSignal },
): Promise<GenerateSiteResult> {
  onProgress({ stage: "streaming", detail: "Asking the taOS agent to design your site..." });

  let raw = "";
  let streamError: string | null = null;
  await streamTaosAgentChat(
    [
      { role: "system", content: buildWebGenerationSystemPrompt() },
      { role: "user", content: prompt },
    ],
    (delta) => {
      raw += delta;
    },
    (message) => {
      streamError = message;
    },
    opts,
  );
  if (streamError) throw new Error(streamError);

  onProgress({ stage: "parsing", detail: "Reading the response..." });
  const site = parseSite(raw);

  if (site) {
    onProgress({ stage: "done", detail: "Ready." });
    return { site, usedFallback: false, parseNotice: null };
  }

  onProgress({ stage: "done", detail: "Ready." });
  return {
    site: fallbackSite(prompt),
    usedFallback: true,
    parseNotice:
      "The agent's response could not be parsed into a site, so the closest starter template was used instead.",
  };
}
