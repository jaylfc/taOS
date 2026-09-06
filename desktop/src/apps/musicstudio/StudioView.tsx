import { useMemo } from "react";
import { Square, Play, Check, Loader2, Plus, Trash2 } from "lucide-react";
import { INSTRUMENTS, instrumentColor } from "./instruments";
import { PianoRoll } from "./PianoRoll";
import { StepSequencer } from "./StepSequencer";
import type { Note } from "./types";
import type { StudioEngineApi } from "./use-studio-engine";

export interface StudioViewProps {
  engine: StudioEngineApi;
  onSave: () => void;
  saving: boolean;
  saveStatus: "idle" | "saved" | "error";
}

const BAR_WIDTH = 72;

export function StudioView({ engine, onSave, saving, saveStatus }: StudioViewProps) {
  const {
    song,
    playing,
    positionLabel,
    play,
    stop,
    setTempo,
    setSongName,
    selectedTrackId,
    setSelectedTrackId,
    selectedClipId,
    setSelectedClipId,
    addTrack,
    removeTrack,
    updateTrack,
    addClip,
    updateClipNotes,
  } = engine;

  const selectedTrack = song.tracks.find((t) => t.id === selectedTrackId) ?? null;
  const selectedClip = selectedTrack
    ? (selectedTrack.clips.find((c) => c.id === selectedClipId) ?? selectedTrack.clips[0] ?? null)
    : null;

  const totalBars = useMemo(() => {
    const furthest = song.tracks
      .flatMap((t) => t.clips)
      .reduce((max, c) => Math.max(max, c.startBar + c.lengthBars), 0);
    return Math.max(12, furthest + 4);
  }, [song.tracks]);
  const ruler = useMemo(() => Array.from({ length: totalBars }, (_, i) => i + 1), [totalBars]);

  const handleLaneClick = (trackId: string, bar: number, existingClipId: string | undefined) => {
    setSelectedTrackId(trackId);
    if (existingClipId) {
      setSelectedClipId(existingClipId);
    } else {
      const newClipId = addClip(trackId, bar);
      setSelectedClipId(newClipId);
    }
  };

  const handleChangeNotes = (notes: Note[]) => {
    if (!selectedTrack || !selectedClip) return;
    updateClipNotes(selectedTrack.id, selectedClip.id, notes);
  };

  const isDrumTrack = selectedTrack ? INSTRUMENTS.find((i) => i.id === selectedTrack.instrument)?.category === "Drums" : false;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* transport bar */}
      <div
        className="flex flex-none items-center gap-3.5 border-b border-shell-border bg-shell-bg-deep px-5"
        style={{ height: "58px" }}
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Stop"
            onClick={stop}
            className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-shell-border bg-shell-surface text-shell-text"
          >
            <Square size={16} />
          </button>
          <button
            type="button"
            aria-label={playing ? "Playing" : "Play"}
            aria-pressed={playing}
            onClick={play}
            className="flex h-9 w-9 items-center justify-center rounded-[10px] text-white"
            style={{ background: "linear-gradient(135deg, var(--color-accent-strong, #a9b0c2), var(--color-accent, #8b92a3))" }}
          >
            <Play size={16} fill="currentColor" />
          </button>
        </div>

        <div className="ml-1.5 flex items-center gap-4">
          <span className="font-mono text-[19px] font-bold tracking-[-0.01em] tabular-nums">{positionLabel}</span>
          <div className="flex flex-col leading-[1.1]">
            <input
              aria-label="Tempo (BPM)"
              type="number"
              min={20}
              max={300}
              value={song.tempo}
              onChange={(e) => setTempo(Number(e.target.value))}
              className="w-11 bg-transparent text-[14px] font-bold tabular-nums outline-none"
            />
            <span className="text-[9.5px] uppercase tracking-[0.06em] text-shell-text-tertiary">BPM</span>
          </div>
          <div className="flex flex-col leading-[1.1]">
            <span className="text-[14px] font-bold tabular-nums">{song.timeSig}</span>
            <span className="text-[9.5px] uppercase tracking-[0.06em] text-shell-text-tertiary">Time</span>
          </div>
          <div className="flex flex-col leading-[1.1]">
            <span className="text-[14px] font-bold">{song.key}</span>
            <span className="text-[9.5px] uppercase tracking-[0.06em] text-shell-text-tertiary">Key</span>
          </div>
          <input
            aria-label="Song name"
            value={song.name}
            onChange={(e) => setSongName(e.target.value)}
            className="w-40 bg-transparent text-[13px] font-semibold text-shell-text-secondary outline-none"
          />
        </div>

        <div className="ml-auto">
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-[10px] border border-shell-border bg-shell-surface px-3 py-[7px] disabled:opacity-60"
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin text-shell-text-secondary" />
            ) : (
              <Check size={14} className="text-shell-text-secondary" />
            )}
            <span className="text-[11.5px] font-semibold text-shell-text-secondary">
              {saving ? "Saving..." : saveStatus === "error" ? "Save failed -- retry" : "Save"}
            </span>
          </button>
        </div>
      </div>

      {/* arrange area */}
      <div className="flex min-h-0 flex-1">
        {/* track list column */}
        <div className="w-[188px] flex-none overflow-auto border-r border-shell-border bg-shell-bg-deep">
          <div className="flex h-7 items-center justify-between border-b border-shell-border px-3">
            <span className="text-[10px] font-bold uppercase tracking-[0.05em] text-shell-text-tertiary">Tracks</span>
            <button type="button" aria-label="Add track" onClick={() => addTrack()} className="text-shell-text-tertiary hover:text-shell-text">
              <Plus size={13} />
            </button>
          </div>

          {song.tracks.map((track) => (
            <div
              key={track.id}
              onClick={() => setSelectedTrackId(track.id)}
              className={`flex cursor-pointer flex-col justify-center gap-1.5 border-b border-shell-border px-3 py-[9px] ${track.id === selectedTrackId ? "bg-shell-surface" : ""}`}
              style={{ height: "62px" }}
            >
              <div className="flex items-center gap-2">
                <span className="h-[9px] w-[9px] flex-none rounded-[3px]" style={{ background: instrumentColor(track.instrument) }} />
                <span className="truncate text-[12.5px] font-semibold">{track.name}</span>
                <div className="ml-auto flex gap-1">
                  <button
                    type="button"
                    aria-label={`${track.muted ? "Unmute" : "Mute"} ${track.name}`}
                    aria-pressed={track.muted}
                    onClick={(e) => {
                      e.stopPropagation();
                      updateTrack(track.id, { muted: !track.muted });
                    }}
                    className={`flex h-[18px] w-[18px] items-center justify-center rounded-[5px] text-[9.5px] font-extrabold ${track.muted ? "bg-accent text-white" : "bg-shell-surface-active text-shell-text-tertiary"}`}
                  >
                    M
                  </button>
                  <button
                    type="button"
                    aria-label={`${track.soloed ? "Unsolo" : "Solo"} ${track.name}`}
                    aria-pressed={track.soloed}
                    onClick={(e) => {
                      e.stopPropagation();
                      updateTrack(track.id, { soloed: !track.soloed });
                    }}
                    className={`flex h-[18px] w-[18px] items-center justify-center rounded-[5px] text-[9.5px] font-extrabold ${track.soloed ? "bg-accent text-white" : "bg-shell-surface-active text-shell-text-tertiary"}`}
                  >
                    S
                  </button>
                </div>
              </div>
              <div
                role="slider"
                aria-label={`${track.name} volume`}
                aria-valuenow={track.volume}
                aria-valuemin={0}
                aria-valuemax={100}
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  const rect = e.currentTarget.getBoundingClientRect();
                  const pct = Math.round(((e.clientX - rect.left) / rect.width) * 100);
                  updateTrack(track.id, { volume: Math.max(0, Math.min(100, pct)) });
                }}
                className="relative h-1 cursor-pointer rounded-full bg-shell-surface-active"
              >
                <span className="absolute inset-y-0 left-0 rounded-full bg-shell-text-tertiary" style={{ width: `${track.volume}%` }} />
              </div>
            </div>
          ))}
        </div>

        {/* scrolling timeline */}
        <div
          className="relative min-w-0 flex-1 overflow-auto"
          style={{
            background: `linear-gradient(90deg, var(--color-shell-border, rgba(255,255,255,0.08)) 1px, transparent 1px) 0 0 / ${BAR_WIDTH}px 100%, var(--color-shell-bg, #1d1d1f)`,
          }}
        >
          <div className="sticky top-0 z-10 flex h-7 border-b border-shell-border bg-shell-bg-deep">
            {ruler.map((b) => (
              <div key={b} className="flex flex-none items-center border-r border-shell-border pl-[7px] text-[10px] font-bold text-shell-text-tertiary" style={{ width: BAR_WIDTH }}>
                {b}
              </div>
            ))}
          </div>

          {song.tracks.map((track) => (
            <div
              key={track.id}
              className="relative cursor-pointer border-b border-shell-border"
              style={{ height: "62px", width: totalBars * BAR_WIDTH }}
              onClick={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                const bar = Math.floor((e.clientX - rect.left) / BAR_WIDTH);
                const clip = track.clips.find((c) => bar >= c.startBar && bar < c.startBar + c.lengthBars);
                handleLaneClick(track.id, bar, clip?.id);
              }}
            >
              {track.clips.map((clip) => (
                <div
                  key={clip.id}
                  className={`absolute top-[9px] flex items-end overflow-hidden rounded-[8px] border border-white/[0.18] px-[7px] pb-[5px] ${clip.id === selectedClip?.id && track.id === selectedTrackId ? "outline outline-2 outline-offset-[-1px] outline-white" : ""}`}
                  style={{
                    left: clip.startBar * BAR_WIDTH,
                    width: clip.lengthBars * BAR_WIDTH,
                    height: "44px",
                    background: instrumentColor(track.instrument),
                    opacity: track.muted ? 0.5 : 1,
                    boxShadow: "0 3px 10px rgba(0,0,0,0.3)",
                  }}
                >
                  <span className="absolute left-2 top-[5px] text-[9.5px] font-bold text-white/90">
                    {clip.name}
                    {track.muted ? " (muted)" : ""}
                  </span>
                </div>
              ))}
            </div>
          ))}

          {song.tracks.length === 0 && (
            <div className="flex h-40 items-center justify-center text-[13px] text-shell-text-tertiary">
              No tracks yet -- add one from the track list.
            </div>
          )}
        </div>

        {/* right inspector */}
        <div className="flex w-[236px] flex-none flex-col gap-4 overflow-auto border-l border-shell-border p-4">
          {selectedTrack ? (
            <>
              <div className="flex items-center gap-[9px] text-[14px] font-bold tracking-[-0.01em]">
                <span className="h-[11px] w-[11px] flex-none rounded-[4px]" style={{ background: instrumentColor(selectedTrack.instrument) }} />
                {selectedTrack.name}
              </div>

              <label className="flex flex-col gap-1.5 text-[11px] text-shell-text-tertiary">
                Instrument
                <select
                  value={selectedTrack.instrument}
                  onChange={(e) => updateTrack(selectedTrack.id, { instrument: e.target.value })}
                  className="rounded-lg border border-shell-border bg-shell-surface px-2 py-1.5 text-[12.5px] text-shell-text"
                >
                  {INSTRUMENTS.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1.5 text-[11px] text-shell-text-tertiary">
                Pan
                <input
                  type="range"
                  min={-1}
                  max={1}
                  step={0.05}
                  value={selectedTrack.pan}
                  onChange={(e) => updateTrack(selectedTrack.id, { pan: Number(e.target.value) })}
                />
              </label>

              <button
                type="button"
                onClick={() => removeTrack(selectedTrack.id)}
                className="mt-auto flex items-center justify-center gap-1.5 rounded-lg border border-shell-border px-3 py-2 text-[12px] font-semibold text-red-400 hover:bg-red-500/10"
              >
                <Trash2 size={13} />
                Remove track
              </button>
            </>
          ) : (
            <p className="text-[12px] text-shell-text-tertiary">Select a track to edit it.</p>
          )}
        </div>
      </div>

      {/* bottom editor: piano roll for melodic tracks, step sequencer for drums */}
      {isDrumTrack ? (
        <StepSequencer clip={selectedClip} onChangeNotes={handleChangeNotes} />
      ) : (
        <PianoRoll clip={selectedClip} onChangeNotes={handleChangeNotes} color={selectedTrack ? instrumentColor(selectedTrack.instrument) : undefined} />
      )}
    </div>
  );
}
