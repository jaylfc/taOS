/** System prompt for the web-authoring agent -- used by generate-site.ts to
 *  turn a free-text prompt into a real `Site` document (see ./types) via the
 *  taos-agent chat stream, instead of only keyword-matching a template.
 *
 *  The model must respond with a SINGLE JSON object matching `Site` exactly
 *  so the response can be parsed and validated with `isValidSite()` the same
 *  way a saved/opened site is. Anything that fails to parse or validate is
 *  never shown as a success -- the caller falls back to matchTemplate(). */

const SECTION_SCHEMA = `Each entry in "sections" is one of these 7 types (exact field names, all required):

- {"id": string, "type": "hero", "content": {"eyebrow": string, "heading": string, "subheading": string, "ctaLabel": string, "image": ""}}
- {"id": string, "type": "features", "content": {"heading": string, "items": [{"title": string, "body": string}, ...] }} (3 items)
- {"id": string, "type": "textBlock", "content": {"heading": string, "body": string}}
- {"id": string, "type": "gallery", "content": {"heading": string, "images": ["", "", "", "", "", ""]}}
- {"id": string, "type": "cta", "content": {"heading": string, "body": string, "buttonLabel": string}}
- {"id": string, "type": "contact", "content": {"heading": string, "email": string, "phone": string, "address": string}}
- {"id": string, "type": "footer", "content": {"businessName": string, "tagline": string}}

Never invent an "image" or "images" value -- always leave them as empty strings ("" / ["", "", ...]); the user adds real images later in the editor.`;

const EXAMPLE = `{
  "title": "Fresh Cafe",
  "theme": { "palette": "sand", "font": "serif" },
  "sections": [
    {
      "id": "hero-1",
      "type": "hero",
      "content": {
        "eyebrow": "Now open",
        "heading": "Coffee worth the walk",
        "subheading": "A neighborhood cafe serving single-origin coffee and fresh pastries every morning.",
        "ctaLabel": "See the menu",
        "image": ""
      }
    },
    {
      "id": "features-1",
      "type": "features",
      "content": {
        "heading": "Why people come back",
        "items": [
          { "title": "Roasted weekly", "body": "Small batches, always fresh." },
          { "title": "Cozy space", "body": "Room to work or catch up." },
          { "title": "Local pastries", "body": "Baked next door every morning." }
        ]
      }
    },
    {
      "id": "footer-1",
      "type": "footer",
      "content": { "businessName": "Fresh Cafe", "tagline": "Made with taOS Web Studio" }
    }
  ]
}`;

/** Build the system prompt for the Generate view's real (non-template) site
 *  generation. Kept as a single exported function (mirrors
 *  gamestudio/game-authoring-prompt.ts) so both the prompt text and the
 *  schema it teaches live in one reviewable place. */
export function buildWebGenerationSystemPrompt(): string {
  return [
    "You are the web-authoring agent for taOS Web Studio. You design a small marketing/landing website from the user's description.",
    "Respond with ONLY a single JSON object -- no prose, no markdown code fence, no explanation before or after it -- matching this schema exactly:",
    `{"title": string, "theme": {"palette": one of "midnight"|"sand"|"forest"|"rose"|"mono", "font": one of "sans"|"serif"|"rounded"|"mono"}, "sections": [ ...section objects... ]}`,
    SECTION_SCHEMA,
    "Pick a palette and font that match the mood of the site (e.g. midnight/sans for a tech product, sand/serif for a cafe). Use 3 to 6 sections, always ending with a footer section. Write real, specific copy for every heading/body/eyebrow field -- never leave them blank or as placeholder text.",
    "Example of a complete, valid response:",
    EXAMPLE,
  ].join("\n\n");
}
