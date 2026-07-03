/* ------------------------------------------------------------------ */
/*  Video Studio — shared types                                        */
/* ------------------------------------------------------------------ */

/** A generated video as surfaced by GET /api/video (list) and the result
 *  of POST /api/video/generate. Mirrors tinyagentos/routes/video.py. */
export interface GeneratedVideo {
  filename: string;
  url: string;
  prompt: string;
  model: string;
  duration: number;
  resolution: string;
  seed: number;
  sizeBytes: number;
}

/** Body posted to /api/video/generate. seed is omitted when the user has
 *  not pinned one, so the backend picks a random seed. */
export interface GenerateParams {
  prompt: string;
  model: string;
  duration: number;
  resolution: string;
  seed?: number;
}

export type StudioView = "create" | "library";

/** Known Wan 2.1 model tiers the wangp backend serves. The backend accepts
 *  any model id string; these are the two real, documented Wan2.1 sizes. */
export const MODEL_OPTIONS = [
  { id: "wan2.1-1.3b", label: "Wan 2.1 1.3B", meta: "Fast" },
  { id: "wan2.1-14b", label: "Wan 2.1 14B", meta: "Higher quality" },
] as const;

export const RESOLUTION_OPTIONS = [
  { id: "480x832", label: "480×832", meta: "Portrait" },
  { id: "832x480", label: "832×480", meta: "Landscape" },
  { id: "576x1024", label: "576×1024", meta: "Portrait HD" },
  { id: "1024x576", label: "1024×576", meta: "Landscape HD" },
] as const;

export const DEFAULT_MODEL = "wan2.1-1.3b";
export const DEFAULT_RESOLUTION = "480x832";
export const DEFAULT_DURATION = 5;
export const MIN_DURATION = 2;
export const MAX_DURATION = 10;
