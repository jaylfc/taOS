/* ------------------------------------------------------------------ */
/*  Images Studio — shared types                                       */
/* ------------------------------------------------------------------ */

/** A generated image as surfaced by /api/images (list) and the result
 *  of /api/images/generate. Mirrors the shape the legacy ImagesApp mapped. */
export interface GeneratedImage {
  id: string;
  url: string;
  prompt: string;
  model: string;
  size: number | string;
  steps: number;
  seed: number;
  guidance: number;
  backend?: string;
  createdAt: string;
}

export interface ModelVariant {
  id: string;
  name: string;
  format?: string;
  size_mb: number;
  min_ram_mb?: number;
  backend?: string[];
  downloaded?: boolean;
  compatibility: "green" | "yellow" | "red";
  download_url?: string;
}

export interface ImageModel {
  id: string;
  name: string;
  description?: string;
  capabilities: string[];
  variants: ModelVariant[];
  has_downloaded_variant?: boolean;
}

/** Parameters posted to /api/images/generate. */
export interface GenerateParams {
  prompt: string;
  model: string;
  variant: string;
  size: string;
  steps: number;
  seed: number;
  guidance_scale: number;
}

export type StudioView = "create" | "library" | "edit";

export type GenerateMode = "single" | "batch";

export type LibraryFilter = "all" | "flux" | "sdxl";

/** Edit tools. Client-side ops are real; generative ops are staged. */
export type EditTool =
  | "adjust"
  | "erase"
  | "inpaint"
  | "removebg"
  | "extend"
  | "upscale"
  | "vary";

/** Tools with no dedicated backend, gated with a "Coming soon" affordance.
 *  erase/inpaint/removebg/extend/upscale are now wired to the editing
 *  backend (IOPaint); "adjust" is client-side (CSS filters). Only "vary"
 *  remains staged — it has no dedicated backend. */
export const STAGED_TOOLS: ReadonlySet<EditTool> = new Set<EditTool>([
  "vary",
]);

/** Which editing capability each tool needs a healthy backend for.
 *  Tools absent from this map (adjust, vary) need no backend. */
export const TOOL_CAPABILITY: Partial<Record<EditTool, EditCapability>> = {
  erase: "image_editing",
  inpaint: "image_editing",
  extend: "image_editing",
  removebg: "background_removal",
  upscale: "upscale",
};

/** Capability gate returned by GET /api/images/edit/capabilities. */
export type EditCapability =
  | "image_editing"
  | "background_removal"
  | "upscale";

export interface EditCapabilities {
  image_editing: boolean;
  background_removal: boolean;
  upscale: boolean;
  /** Per-tier health for image-editing (quality = flux-fill, fast =
   *  iopaint), so the UI can disable Quality instead of letting a request
   *  silently downgrade to the prompt-ignoring iopaint eraser. */
  image_editing_tiers?: { quality: boolean; fast: boolean };
}

export const SIZE_OPTIONS = [256, 512, 768, 1024] as const;

export const STYLE_CHIPS = [
  "Storybook",
  "Photoreal",
  "Watercolor",
  "3D",
  "Anime",
] as const;
