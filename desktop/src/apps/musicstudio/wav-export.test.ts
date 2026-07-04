import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Clip, Note, Song, Track } from "./types";

/* ------------------------------------------------------------------ */
/*  Tone.js and smplr both require a real AudioContext, which jsdom      */
/*  does not implement -- both modules are mocked here, kept in sync     */
/*  with audio-engine.test.ts's mock, plus a fake Tone.Offline that       */
/*  invokes the render callback against a fake offline transport so we   */
/*  can assert what gets scheduled without touching real audio.          */
/* ------------------------------------------------------------------ */

const scheduledEvents: { time: string }[] = [];
const channelInstances: { volume: { value: number }; pan: { value: number } }[] = [];
const offlineCalls: { duration: number; channels?: number; sampleRate?: number }[] = [];

vi.mock("tone", () => {
  class FakeChannel {
    volume: { value: number };
    pan: { value: number };
    constructor(opts: { volume?: number; pan?: number } = {}) {
      this.volume = { value: opts.volume ?? 0 };
      this.pan = { value: opts.pan ?? 0 };
      channelInstances.push(this);
    }
    connect() {
      return this;
    }
    toDestination() {
      return this;
    }
    dispose() {}
  }

  class FakeSynth {
    constructor(_opts?: unknown) {}
    connect() {
      return this;
    }
    triggerAttackRelease() {}
    dispose() {}
  }

  let idCounter = 0;
  function makeOfflineTransport() {
    return {
      bpm: { value: 120 },
      schedule: vi.fn((_cb: (t: number) => void, time: string) => {
        idCounter += 1;
        scheduledEvents.push({ time });
        return idCounter;
      }),
      start: vi.fn(),
    };
  }

  // Mirrors real Tone.Offline: it swaps the module's "current context" for the
  // callback's duration, so Tone.getTransport() called from inside the
  // callback (scheduleTrackNotes does exactly this) resolves to the offline
  // transport rather than a real one.
  let currentTransport: ReturnType<typeof makeOfflineTransport> | null = null;

  return {
    now: vi.fn(() => 0),
    getContext: () => ({ sampleRate: 44100, rawContext: { createGain: () => ({ connect() {}, disconnect() {} }) } }),
    getTransport: () => currentTransport,
    gainToDb: (gain: number) => (gain <= 0 ? -Infinity : 20 * Math.log10(gain)),
    Midi: (pitch: number) => ({ toFrequency: () => 440 * 2 ** ((pitch - 69) / 12) }),
    Channel: FakeChannel,
    MembraneSynth: FakeSynth,
    NoiseSynth: FakeSynth,
    MonoSynth: FakeSynth,
    PolySynth: FakeSynth,
    AMSynth: FakeSynth,
    Synth: FakeSynth,
    Offline: vi.fn(
      async (
        callback: (ctx: { transport: ReturnType<typeof makeOfflineTransport> }) => void | Promise<void>,
        duration: number,
        channels = 2,
        sampleRate = 44100,
      ) => {
        offlineCalls.push({ duration, channels, sampleRate });
        currentTransport = makeOfflineTransport();
        await callback({ transport: currentTransport });
        currentTransport = null;
        return {
          get: () => ({
            numberOfChannels: 2,
            sampleRate,
            length: 8,
            getChannelData: (_c: number) => new Float32Array(8),
          }),
        };
      },
    ),
  };
});

vi.mock("smplr", () => ({
  SplendidGrandPiano: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
  Soundfont: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
}));

import { renderSongToWav, computeSongDurationSeconds, audioBufferToWavBlob } from "./wav-export";

beforeEach(() => {
  scheduledEvents.length = 0;
  channelInstances.length = 0;
  offlineCalls.length = 0;
});

function makeNote(startTick: number, durationTicks: number, pitch = 60): Note {
  return { id: `n-${pitch}-${startTick}`, pitch, startTick, durationTicks, velocity: 0.8 };
}

function makeClip(startBar: number, notes: Note[]): Clip {
  return { id: `clip-${startBar}`, name: "clip", startBar, lengthBars: 1, notes };
}

function makeTrack(id: string, overrides: Partial<Track> = {}): Track {
  return {
    id,
    name: id,
    instrument: "synth-keys",
    clips: [],
    volume: 70,
    pan: 0,
    muted: false,
    soloed: false,
    ...overrides,
  };
}

function makeSong(tracks: Track[], tempo = 120): Song {
  return { id: "song-test", name: "Test Song", tempo, key: "C maj", timeSig: "4/4", tracks };
}

describe("computeSongDurationSeconds", () => {
  it("computes seconds from the last note-off across all tracks, plus the release tail", () => {
    // tempo 120 => 0.5s per beat (480 ticks). One note ending at tick 480 (1 beat).
    const song = makeSong([makeTrack("t1", { clips: [makeClip(0, [makeNote(0, 480)])] })], 120);
    // 1 beat * 0.5s + 2s tail
    expect(computeSongDurationSeconds(song)).toBeCloseTo(2.5, 5);
  });

  it("offsets by the clip's startBar", () => {
    // clip at bar 1 (1920 ticks) + note starting at tick 240, lasting 240 ticks => ends at 2400 ticks = 5 beats = 2.5s @120bpm
    const song = makeSong([makeTrack("t1", { clips: [makeClip(1, [makeNote(240, 240)])] })], 120);
    expect(computeSongDurationSeconds(song)).toBeCloseTo(4.5, 5);
  });

  it("returns just the release tail for a song with no notes", () => {
    expect(computeSongDurationSeconds(makeSong([]))).toBeCloseTo(2, 5);
  });
});

describe("renderSongToWav", () => {
  it("invokes Tone.Offline with the song's full duration and a stereo channel count", async () => {
    const song = makeSong([makeTrack("t1", { clips: [makeClip(0, [makeNote(0, 480)])] })], 120);
    await renderSongToWav(song);
    expect(offlineCalls).toHaveLength(1);
    expect(offlineCalls[0]!.duration).toBeCloseTo(computeSongDurationSeconds(song), 5);
    expect(offlineCalls[0]!.channels).toBe(2);
  });

  it("schedules notes only for unmuted tracks when nothing is soloed", async () => {
    const song = makeSong([
      makeTrack("audible-1", { clips: [makeClip(0, [makeNote(0, 100), makeNote(200, 100)])] }),
      makeTrack("muted", { muted: true, clips: [makeClip(0, [makeNote(0, 100), makeNote(200, 100), makeNote(400, 100)])] }),
      makeTrack("audible-2", { clips: [makeClip(0, [makeNote(0, 100)])] }),
    ]);
    await renderSongToWav(song);
    // 2 notes (audible-1) + 1 note (audible-2); the muted track's 3 notes are skipped.
    expect(scheduledEvents).toHaveLength(3);
    expect(channelInstances).toHaveLength(2);
  });

  it("schedules only the soloed track's notes when a track is soloed", async () => {
    const song = makeSong([
      makeTrack("unsoloed", { clips: [makeClip(0, [makeNote(0, 100), makeNote(200, 100)])] }),
      makeTrack("soloed", { soloed: true, clips: [makeClip(0, [makeNote(0, 100), makeNote(200, 100), makeNote(400, 100)])] }),
      makeTrack("muted-not-soloed", { muted: true, clips: [makeClip(0, [makeNote(0, 100)])] }),
    ]);
    await renderSongToWav(song);
    expect(scheduledEvents).toHaveLength(3);
    expect(channelInstances).toHaveLength(1);
  });

  it("falls back to a synth for a sampled instrument track and reports it as substituted", async () => {
    const song = makeSong([
      makeTrack("rhodes", { instrument: "sampled-piano", clips: [makeClip(0, [makeNote(0, 480)])] }),
    ]);
    const result = await renderSongToWav(song);
    expect(result.substitutedTracks).toEqual(["rhodes"]);
    expect(result.blob).toBeInstanceOf(Blob);
  });

  it("does not report a substitution for a plain synth track", async () => {
    const song = makeSong([makeTrack("keys", { clips: [makeClip(0, [makeNote(0, 480)])] })]);
    const result = await renderSongToWav(song);
    expect(result.substitutedTracks).toEqual([]);
  });

  it("rejects a song with no notes instead of rendering an empty file", async () => {
    await expect(renderSongToWav(makeSong([]))).rejects.toThrow("no notes to export");
    expect(offlineCalls).toHaveLength(0);
  });

  it("rejects a song whose only notes are on muted tracks (nothing audible to render)", async () => {
    const song = makeSong([makeTrack("muted", { muted: true, clips: [makeClip(0, [makeNote(0, 480)])] })]);
    await expect(renderSongToWav(song)).rejects.toThrow("no notes to export");
    expect(offlineCalls).toHaveLength(0);
  });
});

describe("audioBufferToWavBlob", () => {
  it("writes a well-formed RIFF/WAVE/fmt/data header for a small synthetic buffer", async () => {
    const numChannels = 2;
    const sampleRate = 44100;
    const numFrames = 4;
    const left = new Float32Array([0, 0.5, -0.5, 1]);
    const right = new Float32Array([0, -1, 0.25, -0.25]);
    const fakeBuffer = {
      numberOfChannels: numChannels,
      sampleRate,
      length: numFrames,
      getChannelData: (c: number) => (c === 0 ? left : right),
    } as unknown as AudioBuffer;

    const blob = audioBufferToWavBlob(fakeBuffer);
    expect(blob.type).toBe("audio/wav");

    const bytes = new Uint8Array(await blob.arrayBuffer());
    const view = new DataView(bytes.buffer);
    const readStr = (offset: number, len: number) => String.fromCharCode(...bytes.slice(offset, offset + len));

    const dataSize = numFrames * numChannels * 2;
    expect(readStr(0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + dataSize);
    expect(readStr(8, 4)).toBe("WAVE");
    expect(readStr(12, 4)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16); // PCM fmt chunk size
    expect(view.getUint16(20, true)).toBe(1); // PCM format
    expect(view.getUint16(22, true)).toBe(numChannels);
    expect(view.getUint32(24, true)).toBe(sampleRate);
    expect(view.getUint16(34, true)).toBe(16); // bits per sample
    expect(readStr(36, 4)).toBe("data");
    expect(view.getUint32(40, true)).toBe(dataSize);
    expect(bytes.length).toBe(44 + dataSize);

    // Round-trip the first interleaved frame (left then right) back to [-1, 1].
    const firstLeft = view.getInt16(44, true) / 0x7fff;
    const firstRight = view.getInt16(46, true) / 0x7fff;
    expect(firstLeft).toBeCloseTo(0, 3);
    expect(firstRight).toBeCloseTo(0, 3);
    const secondLeft = view.getInt16(48, true) / 0x7fff;
    expect(secondLeft).toBeCloseTo(0.5, 3);
  });
});
