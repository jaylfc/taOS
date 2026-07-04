import { describe, it, expect, vi, beforeEach } from "vitest";
import { TICKS_PER_BEAT, TICKS_PER_BAR, createDefaultSong } from "./types";

/* ------------------------------------------------------------------ */
/*  Tone.js and smplr both require a real AudioContext, which jsdom      */
/*  does not implement -- both modules are mocked here so we can test    */
/*  the engine's MODEL -> schedule logic (tick math, per-track fan-out,  */
/*  mixer param plumbing) without touching real audio.                   */
/* ------------------------------------------------------------------ */

interface ScheduledEvent {
  time: string;
  cb: (t: number) => void;
}

const scheduledEvents: ScheduledEvent[] = [];
const channelInstances: any[] = [];

vi.mock("tone", () => {
  class FakeChannel {
    volume: { value: number };
    pan: { value: number };
    mute: boolean;
    solo: boolean;
    disposed = false;
    constructor(opts: { volume?: number; pan?: number; mute?: boolean; solo?: boolean } = {}) {
      this.volume = { value: opts.volume ?? 0 };
      this.pan = { value: opts.pan ?? 0 };
      this.mute = opts.mute ?? false;
      this.solo = opts.solo ?? false;
      channelInstances.push(this);
    }
    connect() {
      return this;
    }
    toDestination() {
      return this;
    }
    dispose() {
      this.disposed = true;
    }
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
  const transport = {
    bpm: { value: 120 },
    position: "0:0:0" as string,
    state: "stopped" as "stopped" | "started",
    schedule: vi.fn((cb: (t: number) => void, time: string) => {
      idCounter += 1;
      scheduledEvents.push({ time, cb });
      return idCounter;
    }),
    clear: vi.fn(),
    cancel: vi.fn(),
    start: vi.fn(function (this: typeof transport) {
      transport.state = "started";
    }),
    stop: vi.fn(function (this: typeof transport) {
      transport.state = "stopped";
    }),
  };

  const destination = { volume: { value: 0 } };

  return {
    start: vi.fn(async () => {}),
    now: vi.fn(() => 0),
    getTransport: () => transport,
    getContext: () => ({ rawContext: { createGain: () => ({ connect() {}, disconnect() {} }) } }),
    getDestination: () => destination,
    connect: vi.fn(),
    gainToDb: (gain: number) => (gain <= 0 ? -Infinity : 20 * Math.log10(gain)),
    Midi: (pitch: number) => ({ toFrequency: () => 440 * 2 ** ((pitch - 69) / 12) }),
    Channel: FakeChannel,
    MembraneSynth: FakeSynth,
    NoiseSynth: FakeSynth,
    MonoSynth: FakeSynth,
    PolySynth: FakeSynth,
    AMSynth: FakeSynth,
    Synth: FakeSynth,
  };
});

vi.mock("smplr", () => ({
  SplendidGrandPiano: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
  Soundfont: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
}));

import { AudioEngine, ticksToTransportPosition, volumeToDb } from "./audio-engine";
import * as Tone from "tone";

beforeEach(() => {
  scheduledEvents.length = 0;
  channelInstances.length = 0;
});

describe("ticksToTransportPosition", () => {
  it("converts tick 0 to the start of bar 0", () => {
    expect(ticksToTransportPosition(0)).toBe("0:0:0");
  });

  it("converts one beat to beat 1 of bar 0", () => {
    expect(ticksToTransportPosition(TICKS_PER_BEAT)).toBe("0:1:0");
  });

  it("converts one bar to bar 1", () => {
    expect(ticksToTransportPosition(TICKS_PER_BAR)).toBe("1:0:0");
  });

  it("converts a sixteenth note to the sixteenth column", () => {
    expect(ticksToTransportPosition(TICKS_PER_BEAT / 4)).toBe("0:0:1");
  });
});

describe("volumeToDb", () => {
  it("maps 100 (full volume) to 0dB", () => {
    expect(volumeToDb(100)).toBeCloseTo(0, 5);
  });

  it("maps 0 to a very low (but finite) dB floor rather than -Infinity", () => {
    expect(volumeToDb(0)).toBeLessThan(-60);
    expect(Number.isFinite(volumeToDb(0))).toBe(true);
  });

  it("clamps out-of-range input", () => {
    expect(volumeToDb(1000)).toBeCloseTo(volumeToDb(100), 5);
  });
});

describe("AudioEngine.loadSong", () => {
  it("sets the transport tempo from the song", () => {
    const engine = new AudioEngine();
    const song = createDefaultSong();
    song.tempo = 140;
    engine.loadSong(song);
    expect(Tone.getTransport().bpm.value).toBe(140);
  });

  it("schedules exactly one transport event per note across all tracks", () => {
    const engine = new AudioEngine();
    const song = createDefaultSong();
    const totalNotes = song.tracks.flatMap((t) => t.clips).flatMap((c) => c.notes).length;
    engine.loadSong(song);
    expect(scheduledEvents.length).toBe(totalNotes);
  });

  it("builds one Channel per track with its initial volume/pan/mute", () => {
    const engine = new AudioEngine();
    const song = createDefaultSong();
    engine.loadSong(song);
    expect(channelInstances.length).toBe(song.tracks.length);
    const drumChannel = channelInstances[0];
    expect(drumChannel.volume.value).toBeCloseTo(volumeToDb(song.tracks[0].volume), 5);
  });

  it("rebuilding for a new song clears the previous schedule", () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    const firstCount = scheduledEvents.length;
    expect(firstCount).toBeGreaterThan(0);
    engine.loadSong(createDefaultSong());
    // cancel() should have been invoked to drop the old schedule before rebuilding
    expect(Tone.getTransport().cancel).toHaveBeenCalled();
  });
});

describe("AudioEngine.setTrackParam", () => {
  it("updates the live channel without rebuilding the schedule", () => {
    const engine = new AudioEngine();
    const song = createDefaultSong();
    engine.loadSong(song);
    scheduledEvents.length = 0;

    const trackId = song.tracks[0].id;
    engine.setTrackParam(trackId, { volume: 10, pan: 0.5, muted: true, soloed: true });

    const channel = channelInstances[0];
    expect(channel.volume.value).toBeCloseTo(volumeToDb(10), 5);
    expect(channel.pan.value).toBe(0.5);
    expect(channel.mute).toBe(true);
    expect(channel.solo).toBe(true);
    // no new notes scheduled just from a mixer tweak
    expect(scheduledEvents.length).toBe(0);
  });

  it("is a no-op for an unknown track id", () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    expect(() => engine.setTrackParam("does-not-exist", { volume: 50 })).not.toThrow();
  });
});

describe("AudioEngine transport controls", () => {
  it("play() starts the AudioContext and the transport", async () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    await engine.play();
    expect(Tone.start).toHaveBeenCalled();
    expect(Tone.getTransport().start).toHaveBeenCalled();
  });

  it("stop() stops the transport and resets position", () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    engine.stop();
    expect(Tone.getTransport().stop).toHaveBeenCalled();
    expect(Tone.getTransport().position).toBe(0);
  });

  it("getPositionLabel formats bar:beat:sixteenth as a 1-indexed label", () => {
    const engine = new AudioEngine();
    Tone.getTransport().position = "2:1:3";
    expect(engine.getPositionLabel()).toBe("003.2.4");
  });

  it("getPositionLabel returns the reset label when the transport reads a bare number (stopped)", () => {
    const engine = new AudioEngine();
    // After stop() Tone can report `position` as bare seconds (no colons);
    // that must not be parsed as bar:beat:sixteenth.
    (Tone.getTransport() as unknown as { position: string | number }).position = 12.5;
    expect(engine.getPositionLabel()).toBe("001.1.1");
  });
});

describe("AudioEngine.rescheduleTrack (incremental note edits)", () => {
  it("reschedules a single track WITHOUT stopping the transport or rebuilding instruments", () => {
    const engine = new AudioEngine();
    const song = createDefaultSong();
    engine.loadSong(song);

    const channelsAfterLoad = channelInstances.length;
    (Tone.getTransport().stop as ReturnType<typeof vi.fn>).mockClear();
    scheduledEvents.length = 0;

    // Edit the bass track's notes (add one) and reschedule just that track.
    const bass = { ...song.tracks[1] };
    bass.clips = bass.clips.map((c) => ({
      ...c,
      notes: [...c.notes, { id: "extra", pitch: 40, startTick: 0, durationTicks: 120, velocity: 0.8 }],
    }));

    engine.rescheduleTrack(bass);

    // Transport was NOT stopped and no new Channel/instrument was created.
    expect(Tone.getTransport().stop).not.toHaveBeenCalled();
    expect(channelInstances.length).toBe(channelsAfterLoad);
    // The track's notes were re-scheduled (its old events cleared, new ones added).
    expect(Tone.getTransport().clear).toHaveBeenCalled();
    expect(scheduledEvents.length).toBe(bass.clips.reduce((n, c) => n + c.notes.length, 0));
  });

  it("is a no-op for a track the engine has no nodes for", () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    expect(engine.hasTrack("ghost")).toBe(false);
    expect(() =>
      engine.rescheduleTrack({ id: "ghost", name: "x", instrument: "synth-keys", clips: [], volume: 70, pan: 0, muted: false, soloed: false }),
    ).not.toThrow();
  });
});

describe("AudioEngine.setMasterVolume", () => {
  it("re-applies the stored master volume after a loadSong() rebuild", () => {
    const engine = new AudioEngine();
    engine.loadSong(createDefaultSong());
    engine.setMasterVolume(30);
    expect(Tone.getDestination().volume.value).toBeCloseTo(volumeToDb(30), 5);

    // Switching songs rebuilds the graph; the master fader must not reset.
    engine.loadSong(createDefaultSong());
    expect(Tone.getDestination().volume.value).toBeCloseTo(volumeToDb(30), 5);
  });
});

describe("AudioEngine.previewInstrument", () => {
  it("disposes the previous preview before starting a new one (no leak)", async () => {
    const engine = new AudioEngine();
    await engine.previewInstrument("synth-lead");
    const firstPreviewChannel = channelInstances[channelInstances.length - 1];
    expect(firstPreviewChannel.disposed).toBe(false);

    await engine.previewInstrument("synth-keys");
    expect(firstPreviewChannel.disposed).toBe(true);
  });
});
