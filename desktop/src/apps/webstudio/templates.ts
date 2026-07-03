import type {
  Section,
  SectionType,
  SectionContent,
  Site,
  Theme,
} from "./types";

/** Unique id for a section. */
export function newSectionId(): string {
  return `sec-${crypto.randomUUID()}`;
}

/** Default editable content for a freshly-added section of a given type. */
export function defaultContent(type: SectionType): SectionContent {
  switch (type) {
    case "hero":
      return {
        eyebrow: "Welcome",
        heading: "A headline that sells the idea",
        subheading: "One clear sentence about what you do and who it helps.",
        ctaLabel: "Get started",
        image: "",
      };
    case "features":
      return {
        heading: "What you get",
        items: [
          { title: "Fast", body: "Describe the first benefit in a line." },
          { title: "Simple", body: "Describe the second benefit in a line." },
          { title: "Yours", body: "Describe the third benefit in a line." },
        ],
      };
    case "textBlock":
      return {
        heading: "About",
        body: "Write a paragraph about your story, your product, or your mission. This block is good for longer copy.",
      };
    case "gallery":
      return { heading: "Gallery", images: ["", "", "", "", "", ""] };
    case "cta":
      return {
        heading: "Ready to begin?",
        body: "A short nudge toward the next step.",
        buttonLabel: "Contact us",
      };
    case "contact":
      return {
        heading: "Get in touch",
        email: "hello@example.com",
        phone: "+1 555 0100",
        address: "123 Main Street, Your City",
      };
    case "footer":
      return { businessName: "Your Business", tagline: "Made with taOS Web Studio" };
  }
}

export function newSection(type: SectionType): Section {
  return { id: newSectionId(), type, content: defaultContent(type) };
}

function section(type: SectionType, content: Partial<SectionContent>): Section {
  return {
    id: newSectionId(),
    type,
    content: { ...defaultContent(type), ...content } as SectionContent,
  };
}

export interface SiteTemplate {
  id: string;
  name: string;
  tagline: string;
  keywords: string[];
  theme: Theme;
  build: () => Section[];
}

export const TEMPLATES: SiteTemplate[] = [
  {
    id: "landing",
    name: "Landing",
    tagline: "Product or app launch page",
    keywords: ["landing", "product", "app", "saas", "launch", "startup", "software"],
    theme: { palette: "midnight", font: "sans" },
    build: () => [
      section("hero", {
        eyebrow: "New",
        heading: "The simplest way to get it done",
        subheading: "A one-page pitch for your product. Say what it is and why it matters.",
        ctaLabel: "Try it free",
      }),
      section("features", { heading: "Why teams choose us" }),
      section("cta", { heading: "Start building today" }),
      section("footer", {}),
    ],
  },
  {
    id: "portfolio",
    name: "Portfolio",
    tagline: "Show your work",
    keywords: ["portfolio", "designer", "photographer", "artist", "work", "creative", "gallery"],
    theme: { palette: "mono", font: "serif" },
    build: () => [
      section("hero", {
        eyebrow: "Portfolio",
        heading: "Selected work",
        subheading: "A short intro about you and the kind of work you make.",
        ctaLabel: "See projects",
      }),
      section("gallery", { heading: "Recent projects" }),
      section("textBlock", { heading: "About me", body: "A paragraph about your practice and approach." }),
      section("contact", {}),
      section("footer", {}),
    ],
  },
  {
    id: "business",
    name: "Business",
    tagline: "Small business site",
    keywords: ["business", "company", "agency", "services", "consulting", "firm", "shop"],
    theme: { palette: "sand", font: "sans" },
    build: () => [
      section("hero", {
        eyebrow: "Established",
        heading: "Trusted work, done right",
        subheading: "Tell customers what you do and the problem you solve for them.",
        ctaLabel: "Get a quote",
      }),
      section("features", { heading: "Our services" }),
      section("textBlock", { heading: "Our story" }),
      section("cta", { heading: "Let's work together" }),
      section("contact", {}),
      section("footer", {}),
    ],
  },
  {
    id: "blog",
    name: "Blog",
    tagline: "Writing and updates",
    keywords: ["blog", "writing", "newsletter", "articles", "journal", "writer", "posts"],
    theme: { palette: "rose", font: "serif" },
    build: () => [
      section("hero", {
        eyebrow: "Journal",
        heading: "Notes, essays and updates",
        subheading: "A short line about what you write and how often.",
        ctaLabel: "Read latest",
      }),
      section("textBlock", { heading: "Latest post", body: "Paste or write your most recent piece here." }),
      section("textBlock", { heading: "Also worth reading", body: "A second highlighted entry." }),
      section("cta", { heading: "Subscribe for new posts", buttonLabel: "Subscribe" }),
      section("footer", {}),
    ],
  },
  {
    id: "event",
    name: "Event",
    tagline: "Conference or meetup",
    keywords: ["event", "conference", "meetup", "wedding", "party", "festival", "workshop"],
    theme: { palette: "forest", font: "rounded" },
    build: () => [
      section("hero", {
        eyebrow: "You're invited",
        heading: "An event you won't want to miss",
        subheading: "Date, place and the one-line reason to come.",
        ctaLabel: "Reserve a seat",
      }),
      section("features", {
        heading: "The day at a glance",
        items: [
          { title: "Talks", body: "Speakers and sessions." },
          { title: "Workshops", body: "Hands-on tracks." },
          { title: "Social", body: "Meet the community." },
        ],
      }),
      section("gallery", { heading: "Last year" }),
      section("cta", { heading: "Grab your ticket", buttonLabel: "Register" }),
      section("contact", { heading: "Questions?" }),
      section("footer", {}),
    ],
  },
  {
    id: "restaurant",
    name: "Restaurant",
    tagline: "Menu and bookings",
    keywords: ["restaurant", "cafe", "food", "menu", "bar", "bakery", "coffee", "dining"],
    theme: { palette: "sand", font: "serif" },
    build: () => [
      section("hero", {
        eyebrow: "Now open",
        heading: "Fresh food, made with care",
        subheading: "A line about your kitchen and what makes it special.",
        ctaLabel: "Book a table",
      }),
      section("gallery", { heading: "On the menu" }),
      section("textBlock", { heading: "Our kitchen", body: "The story behind the food." }),
      section("cta", { heading: "Reserve your table", buttonLabel: "Book now" }),
      section("contact", { heading: "Find us" }),
      section("footer", {}),
    ],
  },
];

export const DEFAULT_TEMPLATE = TEMPLATES[0]!;

/** Build a full editable Site from a template. */
export function siteFromTemplate(tpl: SiteTemplate, title?: string): Site {
  return {
    title: title || `${tpl.name} site`,
    theme: { ...tpl.theme },
    sections: tpl.build(),
  };
}

export function emptySite(): Site {
  return {
    title: "Untitled site",
    theme: { palette: "midnight", font: "sans" },
    sections: [newSection("hero"), newSection("footer")],
  };
}
