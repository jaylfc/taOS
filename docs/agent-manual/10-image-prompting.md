# Image Prompting

<!-- Writing good prompts for the generate_image tool. -->

## Prompt structure

Order matters: subject first, then descriptors, setting, composition, style, lighting.

Example: `a friendly cartoon fox under a tree, autumn leaves, warm light, watercolour illustration, centred, highly detailed`.

## Principles

- Be specific, not long. Concrete nouns beat vague words.
- Front-load what matters. Earlier words carry more weight.
- One clear scene. Don't pack unrelated ideas; generate separate images instead.
- Name the style explicitly: "children's book illustration", "flat minimalist vector logo".
- Match the user's intent, not a generic version.

## Negative prompt

`negative_prompt` removes common faults: `blurry, low quality, jpeg artifacts, watermark, text, signature`. Add `deformed hands, extra fingers` for people/animals.

## Parameters

- **size** — `256x256`, `384x384`, or `512x512`. Use 512x512 for final art.
- **steps** — 1 to 8 (default 4). 6 to 8 for more detail.
- **guidance_scale** — 1 to 20 (default 7.5). Raise when details are ignored; lower if over-baked.
- **seed** — omit for a fresh image. Reuse to tweak a liked image.
- **model** — call `describe_image_capabilities` first; fast NPU for drafts, GPU for final.

## Iterate deliberately

Change one thing at a time (style word, detail, negative term), keep the same seed, and tell the user what changed.
