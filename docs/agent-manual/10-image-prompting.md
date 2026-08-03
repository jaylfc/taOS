<!-- How to write good prompts for the generate_image tool. -->

# Generating good images

A specific, well-ordered prompt beats a vague one. Spend a sentence getting it right
rather than regenerating five times.

## Structure a prompt

Lead with the subject, then layer detail. A reliable order:

1. **Subject** — what it is. "a small red sailboat", "a friendly cartoon fox".
2. **Descriptors** — appearance, colour, material, mood. "weathered wooden hull,
   bright red sail".
3. **Setting / background** — where it is. "on a calm blue lake at sunrise".
4. **Composition** — framing and viewpoint. "wide shot, centred, low angle".
5. **Style** — the look. "watercolour children's book illustration", "flat vector
   art", "photorealistic", "oil painting". Name a concrete style.
6. **Lighting / quality** — "soft warm light, gentle shadows, highly detailed".

Example: `a friendly cartoon fox reading a book under a tree, autumn leaves,
warm soft light, watercolour children's book illustration, centred, highly detailed`.

## Principles

- **Be specific, not long.** Concrete nouns and adjectives beat a wall of vague
  words. "golden retriever puppy on grass" beats "a nice cute lovely beautiful
  amazing dog".
- **Front-load what matters.** Earlier words carry more weight; put the subject
  and the must-have details first.
- **One clear scene.** Don't pack several unrelated ideas into one prompt; the
  model blends them into mush. Generate separate images instead.
- **Name the style explicitly.** For a storybook look say "children's book
  illustration"; for a logo say "flat minimalist vector logo".
- **Match the user's intent.** Describe what they pictured, not a generic version.
  For a book cover, say "book cover, title space at top".

## Use negative_prompt to remove faults

`negative_prompt` (comma-separated) lists what to avoid. Use it for common defects:

- General cleanup: `blurry, low quality, jpeg artifacts, watermark, text, signature`.
- People/animals: add `deformed hands, extra fingers, extra limbs, mutated`.
- Keep a clean style: add `cluttered, busy background` if you want simplicity.

Use it when a first result has a recurring flaw rather than rewriting the whole prompt.

## Parameters (what the tool exposes)

- **size** — `256x256`, `384x384`, or `512x512`. Use 512x512 for final artwork; smaller only for quick drafts.
- **steps** — 1 to 8 (default 4). Backends are tuned for few-step generation; 4 is a good balance, 6-8 for more detail; more is not always better.
- **guidance_scale** — 1 to 20 (default 7.5). How strictly the image follows the prompt. Raise when the model ignores a detail you asked for; lower if results look over-baked or harsh.
- **seed** — omit for a fresh random image. To edit an image the user liked, reuse its `seed` and tweak the prompt so composition stays close.
- **model** — call `describe_image_capabilities` first and pick a model that fits the task: a fast NPU draft for iterating, a GPU model for the final cover. Omit to let the scheduler choose.

## Picking a model by intent

Model families differ: FLUX-style models follow full natural-language sentences;
SDXL-style models like comma-separated phrases and strong style keywords. Text in
the image (a title or label) is unreliable on most models, so keep it short and
quoted, e.g. `a poster titled "Brave Little Fox"`.

## Iterate deliberately

If the first image is close but not right, change one thing at a time (a style word,
a missing detail, a negative term), keep the same seed, and tell the user what changed.
