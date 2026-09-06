import { TICKS_PER_BEAT, localId, type Clip, type Note } from "./types";

/* ------------------------------------------------------------------ */
/*  StepSequencer -- a 16-step grid for drum tracks. Writes to the same */
/*  Clip/Note model as the piano roll, just fixed to 3 GM drum pitches. */
/* ------------------------------------------------------------------ */

const SIXTEENTH = TICKS_PER_BEAT / 4;
const STEPS = 16;
const ROWS: { pitch: number; label: string }[] = [
  { pitch: 36, label: "Kick" },
  { pitch: 41, label: "Snare" },
  { pitch: 42, label: "Hat" },
];

export interface StepSequencerProps {
  clip: Clip | null;
  onChangeNotes: (notes: Note[]) => void;
}

export function StepSequencer({ clip, onChangeNotes }: StepSequencerProps) {
  if (!clip) {
    return (
      <div className="flex h-[128px] flex-none items-center justify-center border-t border-shell-border bg-shell-bg-deep text-[11.5px] text-shell-text-tertiary">
        Select or add a clip to program steps
      </div>
    );
  }

  const toggle = (pitch: number, step: number) => {
    const startTick = step * SIXTEENTH;
    const existing = clip.notes.find((n) => n.pitch === pitch && n.startTick === startTick);
    if (existing) {
      onChangeNotes(clip.notes.filter((n) => n.id !== existing.id));
    } else {
      onChangeNotes([...clip.notes, { id: localId("note"), pitch, startTick, durationTicks: SIXTEENTH, velocity: 0.9 }]);
    }
  };

  return (
    <div className="flex h-[128px] flex-none flex-col justify-center gap-[6px] border-t border-shell-border bg-shell-bg-deep px-4 py-2">
      {ROWS.map((row) => (
        <div key={row.pitch} className="flex items-center gap-2">
          <span className="w-11 flex-none text-[10px] font-semibold uppercase tracking-[0.04em] text-shell-text-tertiary">
            {row.label}
          </span>
          <div className="flex flex-1 gap-[3px]">
            {Array.from({ length: STEPS }, (_, step) => {
              const on = clip.notes.some((n) => n.pitch === row.pitch && n.startTick === step * SIXTEENTH);
              return (
                <button
                  key={step}
                  type="button"
                  aria-pressed={on}
                  aria-label={`${row.label} step ${step + 1}`}
                  onClick={() => toggle(row.pitch, step)}
                  className={`h-6 flex-1 rounded-[4px] border ${
                    on
                      ? "border-transparent bg-accent"
                      : `border-shell-border ${step % 4 === 0 ? "bg-shell-surface-active" : "bg-shell-surface"}`
                  }`}
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
