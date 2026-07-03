/* ------------------------------------------------------------------ */
/*  Game Studio — shared types                                         */
/*                                                                     */
/*  A game is a real, saved thing: an id, some metadata, and a flat set */
/*  of web files persisted server-side under data/games/{id}/. Every    */
/*  view (Create, Editor, Library, Share) reads or writes through the   */
/*  same /api/games endpoints -- see games-api.ts.                      */
/* ------------------------------------------------------------------ */

export type StudioView = "create" | "editor" | "library" | "share";

/** A starter template: a small, real, playable seed file set shipped under
 *  desktop/public/gamestudio-seeds/{id}/. "Generate with AI" hands the seed
 *  files to the agent to customize; "Use template" saves the seed as-is. */
export interface Template {
  id: string;
  title: string;
  genre: string;
  desc: string;
  /** CSS background for the template card + share cover. */
  cover: string;
  /** Filenames (flat, no subdirectories) that make up this template's seed. */
  files: string[];
}

/** Metadata for a saved game, as returned by GET /api/games and the
 *  non-`files` fields of GET /api/games/{id}. */
export interface GameMeta {
  id: string;
  name: string;
  prompt: string;
  template: string;
  created: number;
  updated: number;
}

/** GET /api/games/{id}: metadata plus the full file map. */
export interface GameRecord extends GameMeta {
  files: Record<string, string>;
}

/** A chat turn in the Editor's AI sidebar. */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}
