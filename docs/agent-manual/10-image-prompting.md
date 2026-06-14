<!-- How to write good prompts for the generate_image tool. -->

# Generating good images

When you call `generate_image`, the quality of the result depends mostly on the
prompt. A vague prompt gives a generic image; a specific, well-ordered one gives
what the user actually asked for. Spend a sentence getting it right rather than
regenerating five times.

## Structure a prompt

Lead with the subject, then layer detail. A reliable order:

1. **Subject** — what it is. "a small red sailboat", "a friendly cartoon fox".
2. **Descriptors** — appearance, colour, material, mood. "weathered wooden hull,
   bright red sail".
3. **Setting / background** — where it is. "on a calm blue lake at sunrise".
4. **Composition** — framing and viewpoint. "wide shot, centred, low angle".
5. **Style** — the look. "watercolour children's book illustration", "flat vector
   art", "photorealistic", "oil painting". Naming a concrete style matters more
   than any other single word.
6. **Lighting / quality** — "soft warm light, gentle shadows, highly detailed".

Example: `a friendly cartoon fox reading a book under a tree, autumn leaves,
warm soft light, watercolour children's book illustration, centred, highly detailed`.

## Principles

- **Be specific, not long.** Concrete nouns and adjectives beat a wall of vague
  words. "golden retriever puppy on grass" beats "a nice cute lovely beautiful
  amazing dog".
- **Front-load what matters.** Earlier words carry more weight. Put the subject
  and the must-have details first.
- **One clear scene.** Don't pack several unrelated ideas into one prompt; the
  model blends them into mush. Generate separate images instead.
- **Name the style explicitly.** If the user wants a storybook look, say
  "children's book illustration" or "storybook watercolour". If they want a logo,
  say "flat minimalist vector logo".
- **Match the user's intent.** Ask yourself what they pictured and describe that,
  not a generic version of it. For a book cover, say "book cover, title space at
  the top, central character".

## Use negative_prompt to remove faults

`negative_prompt` lists what to avoid (comma-separated). It is the fix for common
defects:

- General cleanup: `blurry, low quality, jpeg artifacts, watermark, text, signature`.
- People/animals: add `deformed hands, extra fingers, extra limbs, mutated`.
- Keep a clean style: add `cluttered, busy background` if you want simplicity.

Reach for it when a first result has a recurring flaw rather than rewriting the
whole prompt.

## Parameters (what the tool exposes)

- **size** — `256x256`, `384x384`, or `512x512`. Use 512x512 for the final
  artwork; a smaller size is only worth it for a quick rough draft.
- **steps** — 1 to 8 (default 4). These backends are tuned for few-step
  generation; 4 is a good balance, 6 to 8 for a bit more detail. More is not
  always better here.
- **guidance_scale** — 1 to 20 (default 7.5). How strictly the image follows the
  prompt. Lower (2 to 5) is looser and more artistic; higher (8 to 12) sticks to
  the prompt harder. Raise it when the model ignores a detail you asked for;
  lower it if results look over-baked or harsh.
- **seed** — omit for a fresh random image. To make small edits to an image the
  user liked, reuse its returned `seed` and tweak the prompt so the composition
  stays close.
- **model** — call `describe_image_capabilities` first and pick a model that fits
  the task: a fast NPU draft model for iterating, a GPU model for the final cover.
  Omit it to let the scheduler choose.

## Picking a model by intent

Different model families respond to prompts differently:

- **FLUX-style models** follow natural-language sentences well and render text
  reasonably. Write a full descriptive sentence.
- **SDXL-style models** respond well to comma-separated descriptive phrases and
  strong style keywords.
- **Text in the image** (a title, a sign, a label) is unreliable on most models;
  prefer a model noted for text if one is loaded, keep the text very short, and
  put it in quotes, e.g. `a poster with the title "Brave Little Fox"`.

## Iterate deliberately

If the first image is close but not right, change one thing at a time: adjust the
style word, add a missing detail, or add a negative term for the defect, keeping
the same seed. Tell the user what you changed so they can steer.
