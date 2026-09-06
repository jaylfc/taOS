/* ------------------------------------------------------------------ */
/*  Music Studio -- offline WAV bounce                                 */
/*                                                                     */
/*  Renders a Song to a 16-bit PCM WAV Blob using Tone.Offline, which    */
/*  swaps Tone's global AudioContext for an OfflineAudioContext for the  */
/*  duration of its callback. That means the exact same building blocks  */
/*  the live engine uses -- volumeToDb, buildSynthInstrument, and         */
/*  scheduleTrackNotes (all exported from audio-engine.ts) -- resolve to  */
/*  the offline context automatically when called inside that callback,  */
/*  so the bounce is scheduled identically to live playback rather than   */
/*  a re-implementation of it.                                          */
/*                                                                     */
/*  Deviation from live playback: smplr sampled instruments (Rhodes,     */
/*  808) fetch sample audio from a CDN and drive playback off the real    */
/*  wall clock, neither of which works inside an OfflineAudioContext.     */
/*  Tracks using them are rendered with their closest Tone.js synth       */
/*  instead -- see SAMPLED_FALLBACK below -- and the caller is told       */
/*  which tracks were substituted so the UI can surface a one-line        */
/*  notice rather than silently changing the sound.                     */
/* ------------------------------------------------------------------ */

import * as Tone from "tone";
import {
  buildSynthInstrument,
  scheduleTrackNotes,
  volumeToDb,
  type InstrumentHandle,
} from "./audio-engine";
import { findInstrument } from "./instruments";
import { BEATS_PER_BAR, TICKS_PER_BEAT, type InstrumentId, type Song, type Track } from "./types";
import { slugify } from "./songs-api";

/** Extra time rendered past the last note-off so synth releases (and the
 *  808/Rhodes fallback synths' envelopes) ring out instead of being cut off. */
const RELEASE_TAIL_SECONDS = 2;

const SAMPLED_FALLBACK: Record<string, InstrumentId> = {
  "sampled-piano": "synth-keys",
  "sampled-bass": "synth-bass",
};

/** A sampled instrument's closest offline-renderable synth substitute. */
function offlineInstrumentId(instrumentId: InstrumentId): InstrumentId {
  const def = findInstrument(instrumentId);
  if (def.kind !== "sampled") return instrumentId;
  return SAMPLED_FALLBACK[instrumentId] ?? "synth-keys";
}

/** Tracks actually audible in a mixdown: soloed tracks only when any track is
 *  soloed, otherwise every unmuted track -- matching how Tone.Channel's
 *  solo/mute would sound during live playback. */
function audibleTracks(song: Song): Track[] {
  const hasSolo = song.tracks.some((t) => t.soloed);
  return song.tracks.filter((t) => (hasSolo ? t.soloed : !t.muted));
}

/** Seconds from the start of the song to its last note-off, across every
 *  track (audible or not -- duration shouldn't change if you mute a track),
 *  plus a short release tail. */
export function computeSongDurationSeconds(song: Song): number {
  let lastEndTicks = 0;
  for (const track of song.tracks) {
    for (const clip of track.clips) {
      const clipStartTicks = clip.startBar * BEATS_PER_BAR * TICKS_PER_BEAT;
      for (const note of clip.notes) {
        const endTicks = clipStartTicks + note.startTick + note.durationTicks;
        if (endTicks > lastEndTicks) lastEndTicks = endTicks;
      }
    }
  }
  const seconds = (lastEndTicks / TICKS_PER_BEAT) * (60 / song.tempo);
  return seconds + RELEASE_TAIL_SECONDS;
}

export interface RenderResult {
  blob: Blob;
  /** Names of tracks whose sampled instrument was substituted with a synth
   *  for this render (empty if the song used no sampled instruments). */
  substitutedTracks: string[];
}

/** Render `song` to a WAV Blob via Tone.Offline. Rejects if no audible track
 *  has any notes (nothing to render), rather than bouncing silence. */
export async function renderSongToWav(song: Song): Promise<RenderResult> {
  const tracks = audibleTracks(song);
  const hasNotes = tracks.some((t) => t.clips.some((c) => c.notes.length > 0));
  if (!hasNotes) {
    throw new Error("This song has no notes to export.");
  }
  const duration = computeSongDurationSeconds(song);

  const substitutedTracks: string[] = [];
  const sampleRate = Tone.getContext().sampleRate;

  const buffer = await Tone.Offline(({ transport }) => {
    transport.bpm.value = song.tempo;
    for (const track of tracks) {
      const channel = new Tone.Channel({ volume: volumeToDb(track.volume), pan: track.pan }).toDestination();
      const renderInstrumentId = offlineInstrumentId(track.instrument);
      if (renderInstrumentId !== track.instrument) substitutedTracks.push(track.name);
      const instrument: InstrumentHandle = buildSynthInstrument(renderInstrumentId, channel);
      scheduleTrackNotes(track, instrument);
    }
    transport.start(0);
  }, duration, 2, sampleRate);

  // ToneAudioBuffer.get() is AudioBuffer | undefined; surface the friendly UI
  // error path rather than crashing on a null assertion if a render yields no
  // buffer.
  const rendered = buffer.get();
  if (!rendered) {
    throw new Error("The audio renderer produced no output. Please try again.");
  }

  return { blob: audioBufferToWavBlob(rendered), substitutedTracks };
}

/** Encode a native AudioBuffer as a 16-bit PCM WAV Blob (standard RIFF/WAVE
 *  header, interleaved samples -- no external encoder dependency). */
export function audioBufferToWavBlob(buffer: AudioBuffer): Blob {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const numFrames = buffer.length;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numFrames * blockAlign;

  const arrayBuffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(arrayBuffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size (PCM)
  view.setUint16(20, 1, true); // audio format: 1 = PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true); // byte rate
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 8 * bytesPerSample, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  const channelData: Float32Array[] = [];
  for (let c = 0; c < numChannels; c++) channelData.push(buffer.getChannelData(c));

  let offset = 44;
  for (let i = 0; i < numFrames; i++) {
    for (let c = 0; c < numChannels; c++) {
      const sample = Math.max(-1, Math.min(1, channelData[c]![i] ?? 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += bytesPerSample;
    }
  }

  return new Blob([arrayBuffer], { type: "audio/wav" });
}

/** Render `song` and trigger a browser download of `<songname>.wav`. */
export async function exportSongWavFile(song: Song): Promise<RenderResult> {
  const result = await renderSongToWav(song);
  const url = URL.createObjectURL(result.blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(song.name)}.wav`;
  a.click();
  URL.revokeObjectURL(url);
  return result;
}
