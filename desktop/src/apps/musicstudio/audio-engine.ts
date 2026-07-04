/* ------------------------------------------------------------------ */
/*  Music Studio -- audio engine                                       */
/*                                                                     */
/*  A thin wrapper around Tone.js. One Tone.Channel per track owns      */
/*  volume/pan/mute/solo; every clip's notes are scheduled on            */
/*  Tone.Transport so tempo changes retime already-scheduled notes       */
/*  automatically (bar:beat:sixteenth position strings, not raw          */
/*  seconds). Instruments are either Tone.js synths (fully offline) or   */
/*  smplr sampled instruments (fetch samples from smplr's CDN on first   */
/*  use -- see instruments.ts).                                         */
/*                                                                     */
/*  Browser autoplay policy: the AudioContext must be started from a     */
/*  user gesture, so `ensureStarted()` is called lazily on the first     */
/*  `play()` rather than at construction time.                          */
/* ------------------------------------------------------------------ */

import * as Tone from "tone";
import { SplendidGrandPiano, Soundfont } from "smplr";
import { findInstrument } from "./instruments";
import { BEATS_PER_BAR, TICKS_PER_BEAT, type Song, type Track } from "./types";

type TriggerFn = (pitch: number, durationSeconds: number, time: number, velocity: number) => void;

interface InstrumentHandle {
  trigger: TriggerFn;
  dispose: () => void;
}

interface TrackNodes {
  channel: Tone.Channel;
  instrument: InstrumentHandle;
  eventIds: number[];
}

/** 0-100 (mixer-style, like the existing UI's volume bars) -> decibels. */
export function volumeToDb(volume: number): number {
  const gain = Math.max(0, Math.min(100, volume)) / 100;
  return Tone.gainToDb(Math.max(gain, 0.0001));
}

/** Convert an absolute tick count (from the start of the song) into a Tone
 *  "bar:beat:sixteenth" transport position string. All 0-indexed, matching
 *  Tone.Transport's own position notation. */
export function ticksToTransportPosition(absoluteTicks: number): string {
  const ticksPerSixteenth = TICKS_PER_BEAT / 4;
  const totalSixteenths = absoluteTicks / ticksPerSixteenth;
  const totalBeats = Math.floor(totalSixteenths / 4);
  const bar = Math.floor(totalBeats / BEATS_PER_BAR);
  const beat = totalBeats - bar * BEATS_PER_BAR;
  const sixteenth = totalSixteenths - totalBeats * 4;
  return `${bar}:${beat}:${sixteenth}`;
}

function toneTrigger(synth: { triggerAttackRelease: (...args: any[]) => unknown; dispose: () => void }): InstrumentHandle {
  return {
    trigger: (pitch, duration, time, velocity) => {
      synth.triggerAttackRelease(Tone.Midi(pitch).toFrequency(), duration, time, velocity);
    },
    dispose: () => synth.dispose(),
  };
}

/** Build a fully-offline Tone.js synth instrument, connected to `channel`. */
function buildSynthInstrument(instrumentId: string, channel: Tone.Channel): InstrumentHandle {
  switch (instrumentId) {
    case "drum-kit": {
      const kick = new Tone.MembraneSynth().connect(channel);
      const snare = new Tone.NoiseSynth({
        noise: { type: "white" },
        envelope: { attack: 0.001, decay: 0.15, sustain: 0 },
      }).connect(channel);
      const hat = new Tone.NoiseSynth({
        noise: { type: "white" },
        envelope: { attack: 0.001, decay: 0.04, sustain: 0 },
      }).connect(channel);
      return {
        trigger: (pitch, duration, time, velocity) => {
          if (pitch <= 40) kick.triggerAttackRelease("C1", duration, time, velocity);
          else if (pitch === 41) snare.triggerAttackRelease(duration, time, velocity);
          else hat.triggerAttackRelease(duration, time, velocity);
        },
        dispose: () => {
          kick.dispose();
          snare.dispose();
          hat.dispose();
        },
      };
    }
    case "synth-bass": {
      const synth = new Tone.MonoSynth({
        oscillator: { type: "sawtooth" },
        envelope: { attack: 0.02, decay: 0.2, sustain: 0.4, release: 0.3 },
        filterEnvelope: { baseFrequency: 200, octaves: 2, attack: 0.02, decay: 0.2, sustain: 0.4, release: 0.3 },
      }).connect(channel);
      return toneTrigger(synth);
    }
    case "synth-pad": {
      const synth = new Tone.PolySynth(Tone.AMSynth, {
        envelope: { attack: 0.6, decay: 0.4, sustain: 0.8, release: 1.5 },
      }).connect(channel);
      return toneTrigger(synth);
    }
    case "synth-lead": {
      const synth = new Tone.Synth({
        oscillator: { type: "sawtooth" },
        envelope: { attack: 0.01, decay: 0.1, sustain: 0.6, release: 0.2 },
      }).connect(channel);
      return toneTrigger(synth);
    }
    case "synth-fx": {
      const synth = new Tone.NoiseSynth({
        noise: { type: "pink" },
        envelope: { attack: 0.01, decay: 0.5, sustain: 0 },
      }).connect(channel);
      return {
        trigger: (_pitch, duration, time, velocity) => synth.triggerAttackRelease(duration, time, velocity),
        dispose: () => synth.dispose(),
      };
    }
    case "synth-keys":
    default: {
      const synth = new Tone.PolySynth(Tone.Synth, {
        oscillator: { type: "triangle" },
        envelope: { attack: 0.01, decay: 0.3, sustain: 0.2, release: 0.6 },
      }).connect(channel);
      return toneTrigger(synth);
    }
  }
}

/** Build an smplr sampled instrument, bridged into `channel` via a plain
 *  native GainNode (smplr instruments take a native `destination` AudioNode;
 *  `Tone.connect` accepts native nodes as either side of the connection). */
function buildSampledInstrument(instrumentId: string, channel: Tone.Channel): InstrumentHandle {
  const context = Tone.getContext().rawContext as unknown as AudioContext;
  const bridge = context.createGain();
  Tone.connect(bridge, channel);

  const inst =
    instrumentId === "sampled-bass"
      ? Soundfont(context, { instrument: "electric_bass_finger", destination: bridge })
      : SplendidGrandPiano(context, { destination: bridge });

  return {
    trigger: (pitch, duration, time, velocity) => {
      inst.start({ note: pitch, time, duration, velocity: Math.round(Math.max(0, Math.min(1, velocity)) * 127) });
    },
    dispose: () => {
      inst.dispose();
      bridge.disconnect();
    },
  };
}

function buildInstrument(instrumentId: string, channel: Tone.Channel): InstrumentHandle {
  const def = findInstrument(instrumentId);
  return def.kind === "sampled" ? buildSampledInstrument(def.id, channel) : buildSynthInstrument(def.id, channel);
}

export class AudioEngine {
  private started = false;
  private tracks = new Map<string, TrackNodes>();
  /** Stored so it survives a full `loadSong()` rebuild (which recreates every
   *  track Channel and would otherwise leave the shared Destination at its
   *  previous, possibly default, level). */
  private masterVolume = 80;
  /** The single in-flight Sounds-library preview, so a rapid second click
   *  can dispose the first's nodes and cancel its cleanup timer. */
  private preview: { instrument: InstrumentHandle; channel: Tone.Channel; timeout: ReturnType<typeof setTimeout> } | null = null;

  /** Must be called from a user gesture handler (browser autoplay policy). */
  async ensureStarted(): Promise<void> {
    if (this.started) return;
    await Tone.start();
    this.started = true;
  }

  /** Tear down any existing tracks and rebuild the engine graph for `song`. */
  loadSong(song: Song): void {
    Tone.getTransport().stop();
    Tone.getTransport().cancel(0);
    this.teardownTracks();

    Tone.getTransport().bpm.value = song.tempo;
    for (const track of song.tracks) {
      this.buildTrack(track);
    }
    // The Destination is shared and untouched by teardown, but re-apply the
    // stored master level anyway so a load never silently resets the fader.
    Tone.getDestination().volume.value = volumeToDb(this.masterVolume);
  }

  /** Re-schedule one track's notes in place, WITHOUT stopping the transport or
   *  re-instantiating its instrument/channel. This is the hot path for note
   *  edits: editing a note during playback keeps playing and never re-fetches
   *  smplr samples. No-op (falls back to a rebuild caller-side) if the track
   *  has no engine nodes yet. */
  rescheduleTrack(track: Track): void {
    const nodes = this.tracks.get(track.id);
    if (!nodes) return;
    const transport = Tone.getTransport();
    for (const id of nodes.eventIds) transport.clear(id);
    nodes.eventIds = this.scheduleTrackNotes(track, nodes.instrument);
  }

  private scheduleTrackNotes(track: Track, instrument: InstrumentHandle): number[] {
    const eventIds: number[] = [];
    const transport = Tone.getTransport();
    for (const clip of track.clips) {
      for (const note of clip.notes) {
        const absoluteTicks = clip.startBar * BEATS_PER_BAR * TICKS_PER_BEAT + note.startTick;
        const position = ticksToTransportPosition(absoluteTicks);
        const eventId = transport.schedule((time) => {
          const bpm = transport.bpm.value;
          const durationSeconds = (note.durationTicks / TICKS_PER_BEAT) * (60 / bpm);
          instrument.trigger(note.pitch, durationSeconds, time, note.velocity);
        }, position);
        eventIds.push(eventId);
      }
    }
    return eventIds;
  }

  /** True when the engine already has nodes for this track (so a note edit can
   *  go through the incremental `rescheduleTrack` path). */
  hasTrack(trackId: string): boolean {
    return this.tracks.has(trackId);
  }

  private buildTrack(track: Track): void {
    const channel = new Tone.Channel({
      volume: volumeToDb(track.volume),
      pan: track.pan,
      mute: track.muted,
      solo: track.soloed,
    }).toDestination();

    const instrument = buildInstrument(track.instrument, channel);
    const eventIds = this.scheduleTrackNotes(track, instrument);
    this.tracks.set(track.id, { channel, instrument, eventIds });
  }

  private teardownTracks(): void {
    for (const nodes of this.tracks.values()) {
      for (const id of nodes.eventIds) Tone.getTransport().clear(id);
      nodes.instrument.dispose();
      nodes.channel.dispose();
    }
    this.tracks.clear();
  }

  async play(): Promise<void> {
    await this.ensureStarted();
    Tone.getTransport().start();
  }

  stop(): void {
    Tone.getTransport().stop();
    Tone.getTransport().position = 0;
  }

  isPlaying(): boolean {
    return Tone.getTransport().state === "started";
  }

  /** "bar.beat.sixteenth", 1-indexed, e.g. "003.2.1" -- matches the original
   *  transport readout. Parsed from Tone.Transport's "bar:beat:sixteenth"
   *  position string. When stopped, `position` can read back as a bare number
   *  (seconds) with no colons -- guard that so we don't render it as bar 1e6. */
  getPositionLabel(): string {
    const raw = String(Tone.getTransport().position);
    if (!raw.includes(":")) return "001.1.1";
    const [barStr = "0", beatStr = "0", sixteenthStr = "0"] = raw.split(":");
    const bar = Math.trunc(Number(barStr)) + 1;
    const beat = Math.trunc(Number(beatStr)) + 1;
    const sixteenth = Math.trunc(Number(sixteenthStr)) + 1;
    return `${String(bar).padStart(3, "0")}.${beat}.${sixteenth}`;
  }

  setTempo(bpm: number): void {
    Tone.getTransport().bpm.value = bpm;
  }

  setTrackParam(trackId: string, params: Partial<{ volume: number; pan: number; muted: boolean; soloed: boolean }>): void {
    const nodes = this.tracks.get(trackId);
    if (!nodes) return;
    if (params.volume !== undefined) nodes.channel.volume.value = volumeToDb(params.volume);
    if (params.pan !== undefined) nodes.channel.pan.value = params.pan;
    if (params.muted !== undefined) nodes.channel.mute = params.muted;
    if (params.soloed !== undefined) nodes.channel.solo = params.soloed;
  }

  /** 0-100, applied to Tone's shared Destination (every track's Channel
   *  feeds into it via `.toDestination()`). Stored so `loadSong()` can
   *  re-apply it after rebuilding the graph. */
  setMasterVolume(volume: number): void {
    this.masterVolume = volume;
    Tone.getDestination().volume.value = volumeToDb(volume);
  }

  private disposePreview(): void {
    if (!this.preview) return;
    clearTimeout(this.preview.timeout);
    this.preview.instrument.dispose();
    this.preview.channel.dispose();
    this.preview = null;
  }

  /** Preview a single note on an instrument without touching the loaded
   *  song's schedule -- used by the Sounds library. A second click disposes
   *  the previous preview (and cancels its cleanup timer) before starting the
   *  next, so rapid clicks can't leak channels or cut off the newest note. */
  async previewInstrument(instrumentId: string, pitch = 60): Promise<void> {
    await this.ensureStarted();
    this.disposePreview();
    const channel = new Tone.Channel({ volume: volumeToDb(80) }).toDestination();
    const instrument = buildInstrument(instrumentId, channel);
    const now = Tone.now();
    instrument.trigger(pitch, 0.7, now, 0.85);
    const timeout = setTimeout(() => {
      instrument.dispose();
      channel.dispose();
      this.preview = null;
    }, 1500);
    this.preview = { instrument, channel, timeout };
  }

  dispose(): void {
    Tone.getTransport().stop();
    Tone.getTransport().cancel(0);
    this.disposePreview();
    this.teardownTracks();
  }
}
