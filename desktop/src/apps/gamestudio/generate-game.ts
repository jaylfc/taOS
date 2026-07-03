import { streamTaosAgentChat } from "../appstudio/stream-chat";
import { parseFileBlocks } from "./file-blocks";
import { buildCreateSystemPrompt, isVendorFile } from "./game-authoring-prompt";
import { saveGame, slugify, uniqueSuffix } from "./games-api";
import { matchTemplate } from "./match-template";
import { fetchSeedFiles } from "./templates";
import type { GameRecord, Template } from "./types";

/* ------------------------------------------------------------------ */
/*  Real AI game generation                                            */
/*                                                                     */
/*  Loads the chosen (or matched) template's seed files, streams the    */
/*  taos-agent the same way App Studio's Build view does, parses its    */
/*  response with the file-block convention, and saves the result.      */
/*  Vendor files (three.module.js) are always preserved from the seed   */
/*  regardless of what the agent returns. On a parse failure the        */
/*  unmodified seed is saved instead and the caller is told so -- never  */
/*  a fake "customized" result.                                         */
/* ------------------------------------------------------------------ */

export type GenerateStage = "seed" | "streaming" | "saving" | "done";

export interface GenerateProgress {
  stage: GenerateStage;
  detail: string;
}

export interface GenerateGameResult {
  game: GameRecord;
  template: Template;
  usedFallback: boolean;
  parseNotice: string | null;
}

export async function generateGame(
  prompt: string,
  templateOverride: Template | null,
  onProgress: (p: GenerateProgress) => void,
  opts?: { signal?: AbortSignal },
): Promise<GenerateGameResult> {
  const template = templateOverride ?? matchTemplate(prompt);

  onProgress({ stage: "seed", detail: `Loading the ${template.title} starter files...` });
  const seedFiles = await fetchSeedFiles(template);

  onProgress({ stage: "streaming", detail: "Asking the taOS agent to customize your game..." });
  const systemPrompt = buildCreateSystemPrompt(seedFiles);

  let raw = "";
  let streamError: string | null = null;
  await streamTaosAgentChat(
    [
      { role: "system", content: systemPrompt },
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

  const parsed = parseFileBlocks(raw);
  let files: Record<string, string> = { ...seedFiles };
  let usedFallback = false;
  let parseNotice: string | null = null;
  if (parsed.length === 0) {
    usedFallback = true;
    parseNotice =
      "The agent's response could not be parsed into game files, so the unmodified starter template was used instead.";
  } else {
    for (const f of parsed) {
      if (isVendorFile(f.path)) continue;
      files[f.path] = f.content;
    }
  }

  onProgress({ stage: "saving", detail: "Saving your game..." });
  const id = `${slugify(prompt || template.title)}-${uniqueSuffix()}`;
  const name = prompt.trim() ? prompt.trim().slice(0, 60) : template.title;
  const game = await saveGame(id, { name, prompt, template: template.id, files });

  onProgress({ stage: "done", detail: "Ready." });
  return { game, template, usedFallback, parseNotice };
}

/** Save a template as-is (no AI customization) -- the "Use template" path. */
export async function createFromTemplate(template: Template): Promise<GameRecord> {
  const files = await fetchSeedFiles(template);
  const id = `${slugify(template.title)}-${uniqueSuffix()}`;
  return saveGame(id, { name: template.title, prompt: "", template: template.id, files });
}
