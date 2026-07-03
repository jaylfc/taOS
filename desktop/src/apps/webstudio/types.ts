/* ------------------------------------------------------------------ */
/*  Web Studio - shared types (phase 1)                                */
/*                                                                     */
/*  A section-based website builder. Phase 1 ships a genuinely usable  */
/*  editor: template-matched scaffolding, a visual sections editor,    */
/*  a live device preview and a self-contained static-HTML export.     */
/*  Full offline-LLM generation and publish-to-host are later phases,  */
/*  surfaced as honest "coming" affordances, never faked.              */
/* ------------------------------------------------------------------ */

export type StudioView = "generate" | "templates" | "edit" | "preview" | "export";

/** Device the preview stage emulates. All three resize the same render. */
export type DevicePreview = "desktop" | "tablet" | "mobile";

export type SectionType =
  | "hero"
  | "features"
  | "textBlock"
  | "gallery"
  | "cta"
  | "contact"
  | "footer";

export interface HeroContent {
  eyebrow: string;
  heading: string;
  subheading: string;
  ctaLabel: string;
  image: string; // data URI or empty
}

export interface FeatureItem {
  title: string;
  body: string;
}

export interface FeaturesContent {
  heading: string;
  items: FeatureItem[]; // rendered 3-up
}

export interface TextBlockContent {
  heading: string;
  body: string;
}

export interface GalleryContent {
  heading: string;
  images: string[]; // data URIs; empty slots render as placeholders
}

export interface CtaContent {
  heading: string;
  body: string;
  buttonLabel: string;
}

export interface ContactContent {
  heading: string;
  email: string;
  phone: string;
  address: string;
}

export interface FooterContent {
  businessName: string;
  tagline: string;
}

export type SectionContent =
  | HeroContent
  | FeaturesContent
  | TextBlockContent
  | GalleryContent
  | CtaContent
  | ContactContent
  | FooterContent;

export interface Section {
  id: string;
  type: SectionType;
  content: SectionContent;
}

export interface Theme {
  palette: PaletteId;
  font: FontId;
}

export interface Site {
  title: string;
  theme: Theme;
  sections: Section[];
}

/** Shallow shape guard for a `Site` parsed from stored/untrusted JSON.
 *  Checks only the top-level fields a corrupted or pre-migration record
 *  would be missing; it does not validate individual section content. */
// Mirrors the backend MAX_CONTENT_BYTES cap (tinyagentos/routes/web.py) so the
// editor can reject an over-cap site before the save round-trips to a 413.
export const MAX_CONTENT_BYTES = 5 * 1024 * 1024;

export function isValidSite(value: unknown): value is Site {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.title !== "string") return false;
  if (!v.theme || typeof v.theme !== "object") return false;
  if (!Array.isArray(v.sections)) return false;
  // Validate each section shallowly: a corrupted entry must not slip through
  // and render as a stream of "unsupported section" placeholders.
  return v.sections.every((s) => {
    if (!s || typeof s !== "object") return false;
    const sec = s as Record<string, unknown>;
    return typeof sec.id === "string" && typeof sec.type === "string";
  });
}

/* -------------------------- theme catalog ------------------------- */

export type PaletteId = "midnight" | "sand" | "forest" | "rose" | "mono";
export type FontId = "sans" | "serif" | "rounded" | "mono";

export interface PaletteColors {
  bg: string;
  surface: string;
  text: string;
  muted: string;
  accent: string;
  accentText: string;
  border: string;
}

/** Palettes describe the rendered SITE (user content), so literal colors
 *  here are intentional and not app chrome. */
export const PALETTES: Record<PaletteId, { label: string; colors: PaletteColors }> = {
  midnight: {
    label: "Midnight",
    colors: {
      bg: "#0f1420",
      surface: "#182234",
      text: "#eef2fb",
      muted: "#93a1c0",
      accent: "#5b8cff",
      accentText: "#ffffff",
      border: "#26324a",
    },
  },
  sand: {
    label: "Sand",
    colors: {
      bg: "#faf6ef",
      surface: "#ffffff",
      text: "#2b2419",
      muted: "#8a7c63",
      accent: "#c8853a",
      accentText: "#ffffff",
      border: "#e7ddcb",
    },
  },
  forest: {
    label: "Forest",
    colors: {
      bg: "#0f1a14",
      surface: "#16261d",
      text: "#e9f5ec",
      muted: "#8fb39c",
      accent: "#3fbf7f",
      accentText: "#052014",
      border: "#20362a",
    },
  },
  rose: {
    label: "Rose",
    colors: {
      bg: "#fdf4f6",
      surface: "#ffffff",
      text: "#33202a",
      muted: "#9c7783",
      accent: "#d84f77",
      accentText: "#ffffff",
      border: "#f0d9e0",
    },
  },
  mono: {
    label: "Mono",
    colors: {
      bg: "#ffffff",
      surface: "#f5f5f5",
      text: "#161616",
      muted: "#6b6b6b",
      accent: "#161616",
      accentText: "#ffffff",
      border: "#e2e2e2",
    },
  },
};

export const FONTS: Record<FontId, { label: string; stack: string }> = {
  sans: { label: "Sans", stack: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" },
  serif: { label: "Serif", stack: "Georgia, 'Times New Roman', serif" },
  rounded: { label: "Rounded", stack: "'Nunito', 'Quicksand', 'Segoe UI', sans-serif" },
  mono: { label: "Mono", stack: "'SF Mono', 'Fira Code', 'Consolas', monospace" },
};

export const SECTION_LABELS: Record<SectionType, string> = {
  hero: "Hero",
  features: "Features",
  textBlock: "Text block",
  gallery: "Gallery",
  cta: "Call to action",
  contact: "Contact",
  footer: "Footer",
};
