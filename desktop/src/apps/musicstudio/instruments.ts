/* ------------------------------------------------------------------ */
/*  Music Studio -- instrument catalog                                 */
/*                                                                     */
/*  Two kinds of instrument, both driven through the same per-track     */
/*  Tone.Channel (see audio-engine.ts):                                 */
/*   - "synth": built from Tone.js oscillator synths, fully offline.    */
/*   - "sampled": built from smplr (soundfont/sample players). These     */
/*     fetch sample audio from smplr's public CDN on first use, so they  */
/*     are NOT offline -- see the Phase 1 deviation note in the PR.      */
/* ------------------------------------------------------------------ */

import type { InstrumentId } from "./types";

export type InstrumentKind = "synth" | "sampled";
export type InstrumentCategory = "Drums" | "Bass" | "Keys" | "Synths" | "FX";

export interface InstrumentDef {
  id: string;
  name: string;
  category: InstrumentCategory;
  kind: InstrumentKind;
  detail: string;
}

export const INSTRUMENTS: InstrumentDef[] = [
  { id: "drum-kit", name: "Boom Bap Kit", category: "Drums", kind: "synth", detail: "24 hits" },
  { id: "synth-bass", name: "Analog Bass", category: "Bass", kind: "synth", detail: "synth" },
  { id: "sampled-piano", name: "Rhodes Mk I", category: "Keys", kind: "sampled", detail: "electric piano" },
  { id: "synth-pad", name: "Warm Pad", category: "Synths", kind: "synth", detail: "ambient" },
  { id: "synth-lead", name: "Pluck Lead", category: "Synths", kind: "synth", detail: "mono" },
  { id: "synth-fx", name: "Vinyl FX", category: "FX", kind: "synth", detail: "texture" },
  { id: "sampled-bass", name: "Sub 808", category: "Bass", kind: "sampled", detail: "808" },
  { id: "synth-keys", name: "Felt Piano", category: "Keys", kind: "synth", detail: "acoustic" },
];

export function findInstrument(id: InstrumentId): InstrumentDef {
  return INSTRUMENTS.find((i) => i.id === id) ?? INSTRUMENTS[0]!;
}

/** Muted per-category colors -- content, not chrome (same palette as the
 *  original static mock's TRACK_COLORS). */
export const CATEGORY_COLORS: Record<InstrumentCategory, string> = {
  Drums: "var(--ms-tk-drum, #c98b6b)",
  Bass: "var(--ms-tk-bass, #6f8aa8)",
  Keys: "var(--ms-tk-keys, #7faa90)",
  Synths: "var(--ms-tk-pad, #9a87b0)",
  FX: "var(--ms-tk-lead, #c0a86a)",
};

export function instrumentColor(instrumentId: InstrumentId): string {
  return CATEGORY_COLORS[findInstrument(instrumentId).category];
}
