/* ------------------------------------------------------------------ */
/*  File-block convention -- how the AI returns a game's file set       */
/*                                                                     */
/*  The agent is asked (see game-authoring-prompt.ts) to answer with    */
/*  one block per file it created or changed:                          */
/*                                                                     */
/*    ### FILE: game.js                                                */
/*    ```js                                                            */
/*    ...complete file contents...                                     */
/*    ```                                                              */
/*                                                                     */
/*  parseFileBlocks() extracts every such block. It is line-based      */
/*  (not one big regex) so a stray backtick or fence inside a file's    */
/*  own contents cannot derail parsing early. On total parse failure    */
/*  (no blocks found) the caller falls back to the unmodified seed and  */
/*  shows a visible notice -- generation never fakes success.           */
/* ------------------------------------------------------------------ */

export interface ParsedFile {
  path: string;
  content: string;
}

const HEADER_RE = /^###\s*FILE:\s*(.+?)\s*$/;
const FENCE_RE = /^```/;

/** A safe, flat filename: no path separators, no "..", not empty. Mirrors the
 *  backend's traversal guard in routes/games.py so a parsed filename is
 *  guaranteed to be acceptable to PUT /api/games/{id}. */
export function isSafeFilename(name: string): boolean {
  return (
    name.length > 0 &&
    name !== "." &&
    name !== ".." &&
    !name.includes("/") &&
    !name.includes("\\") &&
    !name.includes("..")
  );
}

export function parseFileBlocks(text: string): ParsedFile[] {
  const lines = text.split("\n");
  const files: ParsedFile[] = [];
  let i = 0;
  while (i < lines.length) {
    const headerMatch = HEADER_RE.exec(lines[i] ?? "");
    if (headerMatch) {
      const path = headerMatch[1]!.trim();
      let j = i + 1;
      while (j < lines.length && !FENCE_RE.test(lines[j] ?? "")) j++;
      if (j < lines.length) {
        const contentLines: string[] = [];
        let k = j + 1;
        while (k < lines.length && !FENCE_RE.test(lines[k] ?? "")) {
          contentLines.push(lines[k]!);
          k++;
        }
        if (k < lines.length && isSafeFilename(path)) {
          files.push({ path, content: contentLines.join("\n") });
        }
        i = k + 1;
        continue;
      }
    }
    i++;
  }
  return files;
}
