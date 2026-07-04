import { Midi } from "@tonejs/midi";
import { BEATS_PER_BAR, TICKS_PER_BEAT, type Song } from "./types";
import { slugify } from "./songs-api";

/* ------------------------------------------------------------------ */
/*  Standard MIDI file export. @tonejs/midi's default header uses 480    */
/*  ticks per quarter note -- the same as our own TICKS_PER_BEAT -- so    */
/*  clip/note ticks translate directly with no conversion.               */
/* ------------------------------------------------------------------ */

export function songToMidiBytes(song: Song): Uint8Array {
  const midi = new Midi();
  midi.header.setTempo(song.tempo);

  for (const track of song.tracks) {
    const midiTrack = midi.addTrack();
    midiTrack.name = track.name;
    for (const clip of track.clips) {
      const clipStartTicks = clip.startBar * BEATS_PER_BAR * TICKS_PER_BEAT;
      for (const note of clip.notes) {
        midiTrack.addNote({
          midi: note.pitch,
          ticks: clipStartTicks + note.startTick,
          durationTicks: Math.max(1, note.durationTicks),
          velocity: Math.max(0, Math.min(1, note.velocity)),
        });
      }
    }
  }

  return midi.toArray();
}

export function exportSongMidiFile(song: Song): void {
  const bytes = songToMidiBytes(song);
  // Uint8Array's `buffer` is typed as ArrayBufferLike (may include
  // SharedArrayBuffer) under the current DOM lib, which BlobPart rejects.
  const blob = new Blob([bytes as unknown as BlobPart], { type: "audio/midi" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(song.name)}.mid`;
  a.click();
  URL.revokeObjectURL(url);
}
