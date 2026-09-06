import { instrumentColor } from "./instruments";
import type { StudioEngineApi } from "./use-studio-engine";

/* ------------------------------------------------------------------ */
/*  MixerView -- one channel strip per track (volume fader, pan, mute/    */
/*  solo) plus a master fader, all bound directly to the live engine.    */
/* ------------------------------------------------------------------ */

export interface MixerViewProps {
  engine: StudioEngineApi;
}

function Fader({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  return (
    <label className="flex flex-1 flex-col items-center gap-2">
      <input
        aria-label={label}
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-32 w-6 [writing-mode:vertical-lr] direction-rtl"
        style={{ writingMode: "vertical-lr", direction: "rtl" }}
      />
      <span className="text-[10.5px] tabular-nums text-shell-text-tertiary">{value}</span>
    </label>
  );
}

export function MixerView({ engine }: MixerViewProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]" style={{ height: "54px" }}>
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Mixer</h2>
        <span className="text-[12px] text-shell-text-tertiary">Volume, pan, mute and solo for every track</span>
      </div>

      <div className="flex flex-1 items-stretch gap-4 overflow-auto p-[22px]">
        {engine.song.tracks.map((track) => (
          <div key={track.id} className="flex w-[104px] flex-none flex-col items-center gap-3 rounded-2xl border border-shell-border bg-shell-surface p-3">
            <span className="h-[9px] w-[9px] flex-none rounded-[3px]" style={{ background: instrumentColor(track.instrument) }} />
            <span className="max-w-full truncate text-[12px] font-bold">{track.name}</span>

            <Fader
              label={`${track.name} volume`}
              value={track.volume}
              onChange={(v) => engine.updateTrack(track.id, { volume: v })}
            />

            <label className="flex w-full flex-col items-center gap-1 text-[10px] text-shell-text-tertiary">
              Pan
              <input
                aria-label={`${track.name} pan`}
                type="range"
                min={-1}
                max={1}
                step={0.05}
                value={track.pan}
                onChange={(e) => engine.updateTrack(track.id, { pan: Number(e.target.value) })}
                className="w-full"
              />
            </label>

            <div className="flex gap-1.5">
              <button
                type="button"
                aria-label={`${track.muted ? "Unmute" : "Mute"} ${track.name}`}
                aria-pressed={track.muted}
                onClick={() => engine.updateTrack(track.id, { muted: !track.muted })}
                className={`flex h-6 w-6 items-center justify-center rounded-md text-[10px] font-extrabold ${track.muted ? "bg-accent text-white" : "bg-shell-surface-active text-shell-text-tertiary"}`}
              >
                M
              </button>
              <button
                type="button"
                aria-label={`${track.soloed ? "Unsolo" : "Solo"} ${track.name}`}
                aria-pressed={track.soloed}
                onClick={() => engine.updateTrack(track.id, { soloed: !track.soloed })}
                className={`flex h-6 w-6 items-center justify-center rounded-md text-[10px] font-extrabold ${track.soloed ? "bg-accent text-white" : "bg-shell-surface-active text-shell-text-tertiary"}`}
              >
                S
              </button>
            </div>
          </div>
        ))}

        {engine.song.tracks.length === 0 && (
          <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
            Add tracks in Studio to mix them here.
          </div>
        )}

        <div className="ml-auto flex w-[104px] flex-none flex-col items-center gap-3 rounded-2xl border border-shell-border-strong bg-shell-surface-active p-3">
          <span className="text-[12px] font-bold">Master</span>
          <Fader label="Master volume" value={engine.masterVolume} onChange={engine.setMasterVolume} />
        </div>
      </div>
    </div>
  );
}
