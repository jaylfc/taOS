/* ------------------------------------------------------------------ */
/*  Music Studio -- shared types                                       */
/*                                                                     */
/*  A Song is a real, saved thing: tempo/key/time signature plus a set  */
/*  of tracks, each holding clips of notes. It is plain JSON so it can  */
/*  round-trip through SongStore's `content` column untouched -- see    */
/*  songs-api.ts and tinyagentos/music_songs.py.                        */
/* ------------------------------------------------------------------ */

/** Ticks per quarter note (beat). Fixed for Phase 1 -- all bars are 4/4   */
/** for scheduling math regardless of the display-only `timeSig` string.  */
export const TICKS_PER_BEAT = 480;
export const BEATS_PER_BAR = 4;
export const TICKS_PER_BAR = TICKS_PER_BEAT * BEATS_PER_BAR;

/** An instrument id resolved by the INSTRUMENTS catalog in instruments.ts. */
export type InstrumentId = string;

/** A single note inside a clip. `pitch` is a MIDI note number (0-127).    */
/** `startTick`/`durationTicks` are relative to the clip's own start, in   */
/** TICKS_PER_BEAT units. `velocity` is normalized 0-1.                    */
export interface Note {
  id: string;
  pitch: number;
  startTick: number;
  durationTicks: number;
  velocity: number;
}

/** A clip is a bar-aligned region of notes on one track. `startBar` is    */
/** 0-indexed (bar 0 is the first bar; the UI ruler displays startBar+1).  */
export interface Clip {
  id: string;
  name: string;
  startBar: number;
  lengthBars: number;
  notes: Note[];
}

export interface Track {
  id: string;
  name: string;
  instrument: InstrumentId;
  clips: Clip[];
  /** 0-100, mixer-style. Converted to decibels by the audio engine. */
  volume: number;
  /** -1 (hard left) to 1 (hard right). */
  pan: number;
  muted: boolean;
  soloed: boolean;
}

export interface Song {
  id: string;
  name: string;
  tempo: number;
  key: string;
  timeSig: string;
  tracks: Track[];
}

/** A song that has never been saved to the server carries a client-minted id
 *  with this prefix. `songs-api.saveSong` uses `isLocalSongId` to decide POST
 *  (create) vs PUT (update) -- the one documented place this convention lives.
 *  The server never mints ids with this prefix (it uses "song-…"). */
export const LOCAL_ID_PREFIX = "local-";

export function newLocalSongId(): string {
  return `${LOCAL_ID_PREFIX}${Math.random().toString(36).slice(2, 10)}`;
}

/** True for an in-memory song that has not yet been persisted server-side. */
export function isLocalSongId(id: string): boolean {
  return id.startsWith(LOCAL_ID_PREFIX);
}

export function createEmptySong(name = "Untitled Song"): Song {
  return {
    id: newLocalSongId(),
    name,
    tempo: 92,
    key: "A min",
    timeSig: "4/4",
    tracks: [],
  };
}

let _idCounter = 0;
/** Small, collision-safe id generator for client-created tracks/clips/notes.
 *  Not persisted-id format (that's the server's job for songs themselves). */
export function localId(prefix: string): string {
  _idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${_idCounter}`;
}

function note(pitch: number, startTick: number, durationTicks: number, velocity = 0.85): Note {
  return { id: localId("note"), pitch, startTick, durationTicks, velocity };
}

/** A small, fully-offline demo song (Tone.js synths only, no smplr/CDN
 *  instruments) so a freshly-opened Studio has something real to play,
 *  mirroring the original static mock's Drums/Bass/Keys/Pad/Lead tracks. */
export function createDefaultSong(): Song {
  const beat = TICKS_PER_BEAT;
  return {
    id: newLocalSongId(),
    name: "Untitled Song",
    tempo: 92,
    key: "A min",
    timeSig: "4/4",
    tracks: [
      {
        id: localId("track"),
        name: "Drums",
        instrument: "drum-kit",
        volume: 72,
        pan: 0,
        muted: false,
        soloed: false,
        clips: [
          {
            id: localId("clip"),
            name: "Drums",
            startBar: 0,
            lengthBars: 1,
            notes: [
              note(36, 0, beat / 2), // kick, beat 1
              note(42, 0, beat / 4), note(42, beat / 2, beat / 4), // hats
              note(41, beat, beat / 2), // snare, beat 2
              note(42, beat, beat / 4), note(42, beat * 1.5, beat / 4),
              note(36, beat * 2, beat / 2), // kick, beat 3
              note(42, beat * 2, beat / 4), note(42, beat * 2.5, beat / 4),
              note(41, beat * 3, beat / 2), // snare, beat 4
              note(42, beat * 3, beat / 4), note(42, beat * 3.5, beat / 4),
            ],
          },
        ],
      },
      {
        id: localId("track"),
        name: "Bass",
        instrument: "synth-bass",
        volume: 60,
        pan: 0,
        muted: false,
        soloed: false,
        clips: [
          {
            id: localId("clip"),
            name: "Bassline",
            startBar: 0,
            lengthBars: 1,
            notes: [note(33, 0, beat), note(33, beat * 2, beat)], // A1
          },
        ],
      },
      {
        id: localId("track"),
        name: "Keys",
        instrument: "synth-keys",
        volume: 54,
        pan: 0,
        muted: false,
        soloed: false,
        clips: [
          {
            id: localId("clip"),
            name: "Rhodes",
            startBar: 0,
            lengthBars: 1,
            notes: [note(57, 0, beat * 2), note(60, 0, beat * 2), note(64, 0, beat * 2)], // A minor triad
          },
        ],
      },
      {
        id: localId("track"),
        name: "Pad",
        instrument: "synth-pad",
        volume: 48,
        pan: 0,
        muted: true,
        soloed: false,
        clips: [
          {
            id: localId("clip"),
            name: "Pad",
            startBar: 0,
            lengthBars: 1,
            notes: [note(45, 0, beat * 4), note(52, 0, beat * 4)],
          },
        ],
      },
      {
        id: localId("track"),
        name: "Lead",
        instrument: "synth-lead",
        volume: 66,
        pan: 0,
        muted: false,
        soloed: false,
        clips: [
          {
            id: localId("clip"),
            name: "Lead",
            startBar: 0,
            lengthBars: 1,
            notes: [note(69, 0, beat / 2), note(72, beat, beat / 2), note(76, beat * 2.5, beat / 2)],
          },
        ],
      },
    ],
  };
}
