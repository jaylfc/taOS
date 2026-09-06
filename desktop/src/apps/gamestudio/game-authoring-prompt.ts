/** System prompts for the game-authoring agent -- shared by the Create flow
 *  (generate-game.ts) and the Editor's AI chat sidebar (EditorView.tsx). Both
 *  send the file-block convention below so the response parses the same way
 *  in either place (see file-blocks.ts). */

/** Vendor files the agent must never be asked to reproduce or allowed to
 *  overwrite: they're large (three.js), fixed, and never hand-edited. */
const VENDOR_FILES = new Set(["three.module.js"]);

export function isVendorFile(name: string): boolean {
  return VENDOR_FILES.has(name);
}

const FILE_BLOCK_CONVENTION = `Respond using this exact convention for every file you create or change:

### FILE: <filename>
\`\`\`<language>
<the complete new contents of that file>
\`\`\`

Rules:
- Only emit a block for a file that is new or that you changed. Never re-emit a file you did not touch.
- Never emit vendor/library files such as "three.module.js" -- they are provided automatically and must never be modified.
- Each block must contain the COMPLETE contents of the file, never a diff or a partial snippet.
- Filenames must be flat: no "/", no "\\\\", no "..".
- You may write plain explanation text outside the blocks; it is shown to the user but ignored when saving the game.`;

function fileListing(files: Record<string, string>): string {
  return Object.entries(files)
    .filter(([name]) => !isVendorFile(name))
    .map(([name, content]) => `### FILE: ${name}\n\`\`\`\n${content}\n\`\`\``)
    .join("\n\n");
}

/** System prompt for the initial Create flow: customize the seed template. */
export function buildCreateSystemPrompt(seedFiles: Record<string, string>): string {
  return [
    "You are the game-authoring agent for taOS Game Studio. You customize a small, self-contained HTML5 game (plain JavaScript, no build step, no external network access -- it runs inside a sandboxed iframe that can only load its own files) to match the user's description.",
    "Starter files:",
    fileListing(seedFiles),
    FILE_BLOCK_CONVENTION,
  ].join("\n\n");
}

/** System prompt for an in-Editor chat edit: modify the current file set. */
export function buildEditSystemPrompt(currentFiles: Record<string, string>): string {
  return [
    "You are the game-authoring agent for taOS Game Studio, editing an existing game. The game is plain, self-contained JavaScript/HTML with no build step and no external network access (it runs inside a sandboxed iframe that can only load its own files).",
    "Current files:",
    fileListing(currentFiles),
    FILE_BLOCK_CONVENTION,
  ].join("\n\n");
}
