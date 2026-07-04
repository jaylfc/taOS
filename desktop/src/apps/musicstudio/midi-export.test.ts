import { describe, it, expect } from "vitest";
import { Midi } from "@tonejs/midi";
import { songToMidiBytes } from "./midi-export";
import { createDefaultSong } from "./types";

describe("songToMidiBytes", () => {
  it("round-trips tempo, track count and note positions through @tonejs/midi", () => {
    const song = createDefaultSong();
    const bytes = songToMidiBytes(song);
    expect(bytes).toBeInstanceOf(Uint8Array);
    expect(bytes.length).toBeGreaterThan(0);

    const parsed = new Midi(bytes);
    expect(Math.round(parsed.header.tempos[0].bpm)).toBe(song.tempo);
    expect(parsed.tracks).toHaveLength(song.tracks.length);
    expect(parsed.tracks[0].name).toBe(song.tracks[0].name);

    const totalNotes = song.tracks.flatMap((t) => t.clips).flatMap((c) => c.notes).length;
    const parsedNotes = parsed.tracks.flatMap((t) => t.notes).length;
    expect(parsedNotes).toBe(totalNotes);

    // Bass track's first note starts at bar 0, tick 0.
    const bassTrack = parsed.tracks.find((t) => t.name === "Bass")!;
    expect(bassTrack.notes[0].ticks).toBe(0);
    expect(bassTrack.notes[0].midi).toBe(33);
  });

  it("offsets note ticks by the clip's startBar", () => {
    const song = createDefaultSong();
    // Move the lead clip to bar 2 and verify the exported ticks shift accordingly.
    const leadTrack = song.tracks.find((t) => t.name === "Lead")!;
    leadTrack.clips[0].startBar = 2;

    const bytes = songToMidiBytes(song);
    const parsed = new Midi(bytes);
    const leadOut = parsed.tracks.find((t) => t.name === "Lead")!;
    // bar 2 * 4 beats/bar * 480 ticks/beat = 3840 ticks offset
    expect(leadOut.notes[0].ticks).toBe(3840);
  });
});
