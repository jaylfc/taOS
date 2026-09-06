import { streamTaosAgentChat } from "./stream-chat";
import { parseFileBlocks } from "../gamestudio/file-blocks";
import { buildAppGenerationSystemPrompt } from "./app-authoring-prompt";

/* ------------------------------------------------------------------ */
/*  Real AI app generation                                             */
/*                                                                     */
/*  Streams the taos-agent the same way Game Studio's generateGame()    */
/*  does, asking it to author a self-contained web app as one         */
/*  ### FILE: block per file (file-blocks.ts). On a parse failure, or  */
/*  a response that never emits an index.html entry point, the caller  */
/*  gets a minimal honest starter page instead -- never a fake         */
/*  "generated" result.                                                 */
/* ------------------------------------------------------------------ */

export type GenerateStage = "streaming" | "parsing" | "done";

export interface GenerateProgress {
  stage: GenerateStage;
  detail: string;
}

export interface GenerateAppResult {
  files: Record<string, string>;
  usedFallback: boolean;
  parseNotice: string | null;
}

const FALLBACK_INDEX_HTML = [
  "<!doctype html>",
  "<html>",
  "  <head><title>App</title></head>",
  "  <body>",
  "    <p>The agent's response could not be turned into an app. Try describing it again.</p>",
  "  </body>",
  "</html>",
  "",
].join("\n");

export async function generateApp(
  prompt: string,
  onProgress: (p: GenerateProgress) => void,
  opts?: { signal?: AbortSignal },
): Promise<GenerateAppResult> {
  onProgress({ stage: "streaming", detail: "Asking the taOS agent to build your app..." });

  let raw = "";
  let streamError: string | null = null;
  await streamTaosAgentChat(
    [
      { role: "system", content: buildAppGenerationSystemPrompt() },
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
  const parsed = parseFileBlocks(raw);
  const files: Record<string, string> = {};
  for (const f of parsed) files[f.path] = f.content;

  onProgress({ stage: "done", detail: "Ready." });

  if (parsed.length === 0) {
    return {
      files: { "index.html": FALLBACK_INDEX_HTML },
      usedFallback: true,
      parseNotice:
        "The agent's response could not be parsed into app files, so a minimal starter page was used instead.",
    };
  }
  if (!files["index.html"]) {
    return {
      files: { "index.html": FALLBACK_INDEX_HTML },
      usedFallback: true,
      parseNotice:
        "The agent's response did not include an index.html entry point, so a minimal starter page was used instead.",
    };
  }
  return { files, usedFallback: false, parseNotice: null };
}
