import { useCallback, useEffect, useRef, useState } from "react";
import { AudioEngine } from "./audio-engine";
import { createDefaultSong, localId, type Clip, type InstrumentId, type Note, type Song, type Track } from "./types";
import { findInstrument } from "./instruments";

/* ------------------------------------------------------------------ */
/*  useStudioEngine -- owns the Song model + a single AudioEngine        */
/*  instance for the lifetime of the Music Studio app, and mediates      */
/*  between the two:                                                    */
/*   - track mixer params (volume/pan/mute/solo) are pushed straight to  */
/*     the live engine (no rebuild) so sliders feel instant and don't    */
/*     re-fetch smplr samples on every drag tick.                       */
/*   - note/clip edits reschedule ONLY the affected track in place        */
/*     (`engine.rescheduleTrack`), so editing a note during playback      */
/*     never stops the transport or re-instantiates smplr instruments.    */
/*   - track-graph changes (add/remove track, instrument swap) bump        */
/*     `structureVersion`, which triggers a full `engine.loadSong()`.     */
/* ------------------------------------------------------------------ */

export function useStudioEngine() {
  const engineRef = useRef<AudioEngine | null>(null);
  if (!engineRef.current) engineRef.current = new AudioEngine();

  const [song, setSong] = useState<Song>(() => createDefaultSong());
  const songRef = useRef(song);
  songRef.current = song;

  const [structureVersion, setStructureVersion] = useState(0);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [positionLabel, setPositionLabel] = useState("001.1.1");
  const [masterVolume, setMasterVolumeState] = useState(80);

  useEffect(() => {
    engineRef.current!.loadSong(songRef.current);
    setSelectedTrackId((prev) => prev ?? songRef.current.tracks[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureVersion]);

  useEffect(() => {
    const engine = engineRef.current!;
    return () => engine.dispose();
  }, []);

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      setPositionLabel(engineRef.current!.getPositionLabel());
    }, 120);
    return () => window.clearInterval(id);
  }, [playing]);

  const play = useCallback(() => {
    void engineRef.current!.play().then(() => setPlaying(true));
  }, []);

  const stop = useCallback(() => {
    engineRef.current!.stop();
    setPlaying(false);
    setPositionLabel("001.1.1");
  }, []);

  const setTempo = useCallback((bpm: number) => {
    const clamped = Math.max(20, Math.min(300, Math.round(bpm)));
    engineRef.current!.setTempo(clamped);
    setSong((prev) => ({ ...prev, tempo: clamped }));
  }, []);

  const loadSongRecord = useCallback((next: Song) => {
    setSong(next);
    setSelectedTrackId(next.tracks[0]?.id ?? null);
    setSelectedClipId(null);
    setStructureVersion((v) => v + 1);
  }, []);

  const newSong = useCallback(() => {
    loadSongRecord(createDefaultSong());
  }, [loadSongRecord]);

  const updateTrack = useCallback(
    (trackId: string, patch: Partial<Pick<Track, "name" | "instrument" | "volume" | "pan" | "muted" | "soloed">>) => {
      setSong((prev) => ({
        ...prev,
        tracks: prev.tracks.map((t) => (t.id === trackId ? { ...t, ...patch } : t)),
      }));
      const structural = "instrument" in patch || "name" in patch;
      if (structural) {
        setStructureVersion((v) => v + 1);
      } else {
        engineRef.current!.setTrackParam(trackId, patch);
      }
    },
    [],
  );

  const addTrack = useCallback((instrument: InstrumentId = "synth-keys") => {
    setSong((prev) => ({
      ...prev,
      tracks: [
        ...prev.tracks,
        {
          id: localId("track"),
          name: findInstrument(instrument).name,
          instrument,
          clips: [],
          volume: 70,
          pan: 0,
          muted: false,
          soloed: false,
        },
      ],
    }));
    setStructureVersion((v) => v + 1);
  }, []);

  const removeTrack = useCallback(
    (trackId: string) => {
      setSong((prev) => ({ ...prev, tracks: prev.tracks.filter((t) => t.id !== trackId) }));
      setStructureVersion((v) => v + 1);
      setSelectedTrackId((prev) => (prev === trackId ? null : prev));
    },
    [],
  );

  /** Commit a single-track edit: update the model and reschedule ONLY that
   *  track in the engine (no transport stop, no instrument re-instantiation).
   *  Falls back to a full reload if the engine has no nodes for the track yet
   *  (e.g. it was just added structurally this tick). */
  const applyTrackEdit = useCallback((updated: Song, trackId: string) => {
    setSong(updated);
    const track = updated.tracks.find((t) => t.id === trackId);
    const engine = engineRef.current!;
    if (track && engine.hasTrack(trackId)) {
      engine.rescheduleTrack(track);
    } else {
      setStructureVersion((v) => v + 1);
    }
  }, []);

  const addClip = useCallback(
    (trackId: string, startBar: number) => {
      const clipId = localId("clip");
      const prev = songRef.current;
      const updated: Song = {
        ...prev,
        tracks: prev.tracks.map((t) =>
          t.id === trackId
            ? { ...t, clips: [...t.clips, { id: clipId, name: t.name, startBar, lengthBars: 1, notes: [] }] }
            : t,
        ),
      };
      applyTrackEdit(updated, trackId);
      return clipId;
    },
    [applyTrackEdit],
  );

  const removeClip = useCallback(
    (trackId: string, clipId: string) => {
      const prev = songRef.current;
      const updated: Song = {
        ...prev,
        tracks: prev.tracks.map((t) => (t.id === trackId ? { ...t, clips: t.clips.filter((c) => c.id !== clipId) } : t)),
      };
      applyTrackEdit(updated, trackId);
      setSelectedClipId((cur) => (cur === clipId ? null : cur));
    },
    [applyTrackEdit],
  );

  const updateClipNotes = useCallback(
    (trackId: string, clipId: string, notes: Note[]) => {
      const prev = songRef.current;
      const updated: Song = {
        ...prev,
        tracks: prev.tracks.map((t) =>
          t.id === trackId
            ? { ...t, clips: t.clips.map((c: Clip) => (c.id === clipId ? { ...c, notes } : c)) }
            : t,
        ),
      };
      applyTrackEdit(updated, trackId);
    },
    [applyTrackEdit],
  );

  const previewInstrument = useCallback((instrumentId: string, pitch?: number) => {
    void engineRef.current!.previewInstrument(instrumentId, pitch);
  }, []);

  const setMasterVolume = useCallback((volume: number) => {
    const clamped = Math.max(0, Math.min(100, volume));
    engineRef.current!.setMasterVolume(clamped);
    setMasterVolumeState(clamped);
  }, []);

  return {
    song,
    setSongName: useCallback((name: string) => setSong((prev) => ({ ...prev, name })), []),
    playing,
    positionLabel,
    play,
    stop,
    setTempo,
    selectedTrackId,
    setSelectedTrackId,
    selectedClipId,
    setSelectedClipId,
    addTrack,
    removeTrack,
    updateTrack,
    addClip,
    removeClip,
    updateClipNotes,
    previewInstrument,
    masterVolume,
    setMasterVolume,
    loadSongRecord,
    newSong,
  };
}

export type StudioEngineApi = ReturnType<typeof useStudioEngine>;
