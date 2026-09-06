# Game Studio offline asset-generation stack (design spec)

Status: DRAFT for Jay review (#55). Game Studio already works as an LLM-authored
three.js maker; this adds offline generation of the game's ART + AUDIO assets so a
generated game is not limited to code-drawn primitives. Author: taOS-dev.

## Why this exists

Game Studio v1 is functional: an agent authors a three.js game (HTML + JS file
set) from a prompt, the game previews sandboxed, packages as `.taosapp`, and shares
to the store. Today the visuals are whatever the model can draw with three.js
primitives + procedural code, and audio is absent or web-synth. #55 adds real
asset generation - textures/sprites, sound effects + music, and (stretch) 3D
meshes - all offline, tier-aware, reusing the backends taOS already ships. This
mirrors the Images Studio pattern: the app calls tier-aware backends, degrading
gracefully on low-end hardware.

## Asset types, in ascending difficulty

1. **Textures / sprites / skyboxes (2D images).** Highest value, lowest new cost:
   reuse the EXISTING image-generation backend (SD on the RTX 3060 per the image
   stack; RK image-gen on the NPU as the arms-length low tier). A game asks for
   "a mossy stone texture, tileable" or "a pixel-art spaceship sprite sheet"; the
   backend returns a PNG the game references. Tileable/seamless + transparent-bg
   (sprite) are prompt/pipeline options, not new models.
2. **Audio - SFX + music.** SFX (jump, hit, coin) and short loops. Reuse MusicGen
   for music loops (already in the stack, license-gated CC-BY-NC per the licensing
   work) and a small text-to-SFX path (e.g. a lightweight audio model or a curated
   procedural/sample fallback). Output: ogg/mp3 the game loads.
3. **3D meshes (stretch, gated).** The hard one: text/image-to-3D (TripoSR,
   Shap-E, InstantMesh, or image-to-mesh). Heavy, GPU-bound, quality is rough, and
   glTF export + three.js loading add complexity. Ship LAST, tier-gated to the
   discrete-GPU tier only, behind a clear "experimental" flag. Many good three.js
   games never need generated meshes (primitives + textures go far).

## Design (tier-aware, backend-reusing)

- **Backend contract.** One controller route surface, `routes/game_assets.py`,
  with `POST /api/games/{id}/assets/texture`, `.../assets/audio`, and (later)
  `.../assets/mesh`. Each resolves the tier-appropriate backend the same way the
  Images Studio edit backends do (hardware tier -> concrete backend), writes the
  asset into the game's file set (so it packages + previews with the game), and
  returns the asset path. No new per-asset infra: textures go through the existing
  image backend, audio through MusicGen + the SFX path.
- **Frontend.** Game Studio's Editor gains an "Assets" panel: generate a
  texture/sprite/audio from a prompt, preview it, and insert a reference into the
  current file (the authoring agent already manages the file set, so an inserted
  asset is just another file + a code reference the agent or user wires up).
  Tier-gated UI: unavailable backends show a clear "needs a GPU worker" state
  rather than a broken button (matches the Images Studio + reduce-effects
  precedent).
- **License + provenance.** Reuse the existing license-accept gate (MusicGen /
  FLUX weights are CC-BY-NC; SD/Apache-clean is the default). A game that bundles
  generated assets records their provenance in the package, consistent with the
  app-security-analyzer + provenance work.
- **Offline-first.** Everything runs on the local/worker GPU; no cloud. On a
  GPU-less host (Pi core alone) textures fall back to the NPU RK backend or a
  curated built-in pack; 3D is simply unavailable.

## Slice plan (spec -> build -> harden, per Jay)

- **Slice 1 - textures/sprites** (build first; highest value/lowest cost). Route +
  tier-aware image-backend call + Editor Assets panel (texture/sprite) + tests.
  Live-verify a generated texture appears in a previewed game on the 3060 tier.
- **Slice 2 - audio (SFX + music).** MusicGen loop + SFX path + Assets-panel audio
  tab + license gate + tests.
- **Slice 3 - 3D meshes (experimental, gated).** Text/image-to-3D on the
  discrete-GPU tier only, glTF export, three.js loader wiring, behind an
  experimental flag. Only if Slices 1-2 land clean.
- **Harden (last, per your ordering):** test coverage across the asset routes +
  panel, and screenshot-verify a real generated game with generated assets on the
  Pi.

## Open questions for Jay

1. **SFX model choice** for Slice 2: a dedicated text-to-SFX model vs a
   curated-sample + light-synth fallback. MusicGen covers music loops already;
   SFX is the gap. Recommend starting with the fallback + MusicGen and adding a
   model only if quality demands it.
2. **3D scope (Slice 3):** ship it experimental on the 3060 tier, or defer 3D
   entirely for now and stop at textures + audio? (Textures + audio deliver most
   of the value; 3D is heavy and rough.)
3. Confirm the target build/verify hardware: the RTX 3060 (Fedora worker) for the
   GPU tiers, RK NPU (Pi) for the low tier - consistent with the existing image
   stack.

## Non-goals (v1)

Animation/rigging, video, voice/TTS dialogue, and any cloud generation.
