import { useState } from "react";
import { Play } from "lucide-react";
import { INSTRUMENTS, instrumentColor, type InstrumentCategory } from "./instruments";
import type { StudioEngineApi } from "./use-studio-engine";

const FILTER_PILLS: (InstrumentCategory | "All")[] = ["All", "Drums", "Bass", "Keys", "Synths", "FX"];

export interface SoundsViewProps {
  engine: StudioEngineApi;
}

export function SoundsView({ engine }: SoundsViewProps) {
  const [filter, setFilter] = useState<(typeof FILTER_PILLS)[number]>("All");
  const selectedTrack = engine.song.tracks.find((t) => t.id === engine.selectedTrackId) ?? null;

  const visible = filter === "All" ? INSTRUMENTS : INSTRUMENTS.filter((i) => i.category === filter);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]" style={{ height: "54px" }}>
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Sounds</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {selectedTrack ? `Click to preview, "Use" to assign to ${selectedTrack.name}` : "Select a track in Studio to assign an instrument"}
        </span>
      </div>

      <div className="flex-1 overflow-auto p-[22px]">
        <div className="mb-[18px] flex flex-wrap gap-[9px]">
          {FILTER_PILLS.map((pill) => (
            <button
              key={pill}
              type="button"
              onClick={() => setFilter(pill)}
              aria-pressed={filter === pill}
              className={`rounded-full border px-3.5 py-[7px] text-[12px] font-semibold ${
                filter === pill ? "border-transparent bg-accent text-white" : "border-shell-border bg-shell-surface text-shell-text-secondary"
              }`}
            >
              {pill}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-4 gap-[13px]">
          {visible.map((sound) => (
            <div
              key={sound.id}
              role="button"
              tabIndex={0}
              aria-label={`Preview ${sound.name}`}
              onClick={() => engine.previewInstrument(sound.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") engine.previewInstrument(sound.id);
              }}
              className="flex cursor-pointer flex-col gap-[11px] rounded-[14px] border border-shell-border bg-shell-surface p-[14px] transition-all hover:-translate-y-[3px] hover:border-shell-border-strong"
            >
              <div
                className="flex items-center justify-center gap-[2px] rounded-[10px]"
                style={{ height: "54px", background: `linear-gradient(140deg, ${instrumentColor(sound.id)}, rgba(0,0,0,0.35))` }}
              >
                <Play size={20} className="text-white/85" fill="currentColor" />
              </div>

              <div className="text-[13px] font-bold">{sound.name}</div>
              <div className="mt-[-6px] flex items-center justify-between text-[11px] text-shell-text-tertiary">
                <span>
                  {sound.category} - {sound.detail}
                </span>
                {selectedTrack && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      engine.updateTrack(selectedTrack.id, { instrument: sound.id });
                    }}
                    className="rounded-md border border-shell-border px-2 py-0.5 text-[10.5px] font-bold text-accent hover:bg-accent/10"
                  >
                    Use
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
