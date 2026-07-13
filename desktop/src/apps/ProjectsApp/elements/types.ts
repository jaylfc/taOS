import type { Tab } from "../ProjectWorkspace";

export interface ElementTypeDef {
  /** Human label for the type, shown on the element card and picker. */
  label: string;
  /** Short glyph rendered in the card icon badge. */
  icon: string;
  /** Ordered tabs; the first is the landing tab when an element is opened. */
  defaultTabs: Tab[];
  /** Free-form hints for future view/template wiring (per design doc). */
  appHints: Record<string, unknown>;
}

// The registry is code-level metadata only. The server stores `type` as a
// free string and does not validate against this map, so a newer client can
// introduce a type without a server change. Any unknown type renders as
// generic (see elementType).
export const ELEMENT_TYPES: Record<string, ElementTypeDef> = {
  generic: {
    label: "Element",
    icon: "📦",
    defaultTabs: ["board", "canvas", "files", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: {},
  },
  code: {
    label: "Code repo",
    icon: "💻",
    defaultTabs: ["board", "canvas", "files", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: { repo_url: "" },
  },
  website: {
    label: "Website",
    icon: "🌐",
    defaultTabs: ["board", "canvas", "files", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: { url: "" },
  },
  design: {
    label: "Design collection",
    icon: "🎨",
    defaultTabs: ["canvas", "board", "files", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: {},
  },
  docs: {
    label: "Docs",
    icon: "📄",
    defaultTabs: ["files", "board", "canvas", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: {},
  },
  marketing: {
    label: "Marketing",
    icon: "📣",
    defaultTabs: ["canvas", "files", "board", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: {},
  },
  business: {
    label: "Business planning",
    icon: "📊",
    defaultTabs: ["files", "board", "canvas", "tasks", "messages", "members", "activity", "decisions", "routines"],
    appHints: {},
  },
};

export const ELEMENT_TYPE_ORDER: string[] = [
  "generic",
  "code",
  "website",
  "design",
  "docs",
  "marketing",
  "business",
];

export function elementType(type: string): ElementTypeDef {
  return ELEMENT_TYPES[type] ?? ELEMENT_TYPES.generic!;
}

export function elementTypeLabel(type: string): string {
  return elementType(type).label;
}

export function defaultTabForType(type: string): Tab {
  return elementType(type).defaultTabs[0] ?? "board";
}
