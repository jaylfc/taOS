// Deck model for the Slides view. Persisted as the office doc's `content`
// (kind "slides"): the whole deck is serialized to JSON, mirroring how Calc
// serializes its workbook (see ../calc/workbook.ts) and Write stores its
// Tiptap HTML directly as `content`.

import { randomId } from "@/lib/uid";

export type SlideLayout = "title" | "title-content" | "two-column" | "section-header" | "blank";

export interface Slide {
  id: string;
  layout: SlideLayout;
  title: string;
  body: string;
  bullets: string[];
  imageDataUri?: string;
  notes?: string;
}

export interface Deck {
  version: 1;
  title: string;
  slides: Slide[];
}

export const LAYOUTS: { id: SlideLayout; label: string }[] = [
  { id: "title", label: "Title" },
  { id: "title-content", label: "Title + content" },
  { id: "two-column", label: "Two column" },
  { id: "section-header", label: "Section header" },
  { id: "blank", label: "Blank" },
];

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

function genId(): string {
  return randomId("slide-");
}

export function newSlide(layout: SlideLayout = "title-content"): Slide {
  return { id: genId(), layout, title: "", body: "", bullets: [] };
}

export function blankDeck(): Deck {
  return { version: 1, title: "Untitled deck", slides: [newSlide("title")] };
}

// Inserts an already-constructed slide after `afterId` (or at the end, if
// `afterId` is null or not found). Split out from addSlide so a caller that
// needs to know the new slide's id (e.g. to select it) can build the slide
// first, instead of diffing the before/after arrays to find it.
export function insertSlideAfter(slides: Slide[], afterId: string | null, slide: Slide): Slide[] {
  if (afterId == null) return [...slides, slide];
  const index = slides.findIndex((s) => s.id === afterId);
  if (index === -1) return [...slides, slide];
  const next = [...slides];
  next.splice(index + 1, 0, slide);
  return next;
}

export function addSlide(slides: Slide[], afterId: string | null, layout?: SlideLayout): Slide[] {
  return insertSlideAfter(slides, afterId, newSlide(layout));
}

export function removeSlide(slides: Slide[], id: string): Slide[] {
  if (slides.length <= 1) return slides;
  return slides.filter((s) => s.id !== id);
}

export function updateSlide(slides: Slide[], id: string, patch: Partial<Omit<Slide, "id">>): Slide[] {
  return slides.map((s) => (s.id === id ? { ...s, ...patch } : s));
}

// Moves the slide at `fromIndex` to `toIndex`, clamping so an out-of-range
// index is a no-op-safe move to the nearest valid end rather than throwing or
// silently dropping the slide.
export function reorderSlide(slides: Slide[], fromIndex: number, toIndex: number): Slide[] {
  if (fromIndex < 0 || fromIndex >= slides.length) return slides;
  const clampedTo = Math.max(0, Math.min(toIndex, slides.length - 1));
  if (clampedTo === fromIndex) return slides;
  const next = [...slides];
  // fromIndex was already bounds-checked above, so this splice always
  // returns the removed element.
  const moved = next.splice(fromIndex, 1)[0] as Slide;
  next.splice(clampedTo, 0, moved);
  return next;
}

export function moveSlideBy(slides: Slide[], id: string, delta: number): Slide[] {
  const index = slides.findIndex((s) => s.id === id);
  if (index === -1) return slides;
  return reorderSlide(slides, index, index + delta);
}

export function serializeDeck(deck: Deck): string {
  return JSON.stringify(deck);
}

function isValidSlide(value: unknown): value is Slide {
  if (!value || typeof value !== "object") return false;
  const s = value as Partial<Slide>;
  if (typeof s.id !== "string") return false;
  if (typeof s.title !== "string") return false;
  if (typeof s.body !== "string") return false;
  if (!Array.isArray(s.bullets) || !s.bullets.every((b) => typeof b === "string")) return false;
  if (s.imageDataUri !== undefined && typeof s.imageDataUri !== "string") return false;
  if (s.notes !== undefined && typeof s.notes !== "string") return false;
  const validLayouts: SlideLayout[] = [
    "title",
    "title-content",
    "two-column",
    "section-header",
    "blank",
  ];
  if (!validLayouts.includes(s.layout as SlideLayout)) return false;
  return true;
}

// Parses saved content into a Deck, falling back to a fresh blank deck on
// any malformed or corrupted content instead of crashing the view.
export function parseDeckContent(content: string): Deck {
  if (!content || !content.trim()) return blankDeck();
  try {
    const parsed = JSON.parse(content) as Partial<Deck>;
    if (!parsed || typeof parsed.title !== "string" || !Array.isArray(parsed.slides)) {
      return blankDeck();
    }
    if (parsed.slides.length === 0 || !parsed.slides.every(isValidSlide)) {
      return blankDeck();
    }
    return { version: 1, title: parsed.title, slides: parsed.slides };
  } catch {
    return blankDeck();
  }
}
