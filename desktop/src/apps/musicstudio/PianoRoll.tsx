import { useCallback, useRef, useState } from "react";
import { TICKS_PER_BEAT, localId, type Clip, type Note } from "./types";

/* ------------------------------------------------------------------ */
/*  PianoRoll -- click an empty cell to add a note, click a note to      */
/*  remove it, drag a note to move it. Grid: 36px per sixteenth note,    */
/*  16px per semitone row (matches the original mock's pixel scale).    */
/* ------------------------------------------------------------------ */

const ROW_HEIGHT = 16;
const COL_WIDTH = 36;
const SIXTEENTH = TICKS_PER_BEAT / 4;
const LOWEST_PITCH = 36; // C2
const HIGHEST_PITCH = 96; // C7

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
function pitchLabel(pitch: number): string {
  return `${NOTE_NAMES[pitch % 12]}${Math.floor(pitch / 12) - 1}`;
}
function isBlackKey(pitch: number): boolean {
  return [1, 3, 6, 8, 10].includes(pitch % 12);
}

export interface PianoRollProps {
  clip: Clip | null;
  onChangeNotes: (notes: Note[]) => void;
  color?: string;
}

export function PianoRoll({ clip, onChangeNotes, color = "var(--ms-tk-bass, #6f8aa8)" }: PianoRollProps) {
  const rows = [];
  for (let p = HIGHEST_PITCH; p >= LOWEST_PITCH; p--) rows.push(p);

  const gridRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{ noteId: string; startX: number; startY: number; origTick: number; origPitch: number } | null>(null);
  const [dragPreview, setDragPreview] = useState<{ noteId: string; startTick: number; pitch: number } | null>(null);

  const lengthTicks = (clip?.lengthBars ?? 1) * 4 * TICKS_PER_BEAT;
  const totalCols = Math.max(1, Math.round(lengthTicks / SIXTEENTH));

  const handleGridClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!clip || dragState.current) return;
      const rect = gridRef.current!.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const col = Math.floor(x / COL_WIDTH);
      const rowIndex = Math.floor(y / ROW_HEIGHT);
      const pitch = HIGHEST_PITCH - rowIndex;
      const startTick = col * SIXTEENTH;
      if (pitch < LOWEST_PITCH || pitch > HIGHEST_PITCH || col < 0 || col >= totalCols) return;

      const existing = clip.notes.find((n) => n.pitch === pitch && n.startTick === startTick);
      if (existing) {
        onChangeNotes(clip.notes.filter((n) => n.id !== existing.id));
      } else {
        onChangeNotes([...clip.notes, { id: localId("note"), pitch, startTick, durationTicks: SIXTEENTH, velocity: 0.85 }]);
      }
    },
    [clip, onChangeNotes, totalCols],
  );

  const handleNotePointerDown = useCallback((e: React.PointerEvent, note: Note) => {
    e.stopPropagation();
    let moved = false;
    dragState.current = { noteId: note.id, startX: e.clientX, startY: e.clientY, origTick: note.startTick, origPitch: note.pitch };
    setDragPreview({ noteId: note.id, startTick: note.startTick, pitch: note.pitch });

    const handleMove = (ev: PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const deltaCols = Math.round((ev.clientX - drag.startX) / COL_WIDTH);
      const deltaRows = Math.round((ev.clientY - drag.startY) / ROW_HEIGHT);
      if (deltaCols !== 0 || deltaRows !== 0) moved = true;
      const nextTick = Math.max(0, drag.origTick + deltaCols * SIXTEENTH);
      const nextPitch = Math.max(LOWEST_PITCH, Math.min(HIGHEST_PITCH, drag.origPitch - deltaRows));
      setDragPreview({ noteId: drag.noteId, startTick: nextTick, pitch: nextPitch });
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      const drag = dragState.current;
      dragState.current = null;
      if (!clip || !drag) {
        setDragPreview(null);
        return;
      }
      if (!moved) {
        // A plain click (no drag): remove the note.
        onChangeNotes(clip.notes.filter((n) => n.id !== drag.noteId));
        setDragPreview(null);
        return;
      }
      setDragPreview((current) => {
        if (current && current.noteId === drag.noteId) {
          onChangeNotes(
            clip.notes.map((n) => (n.id === drag.noteId ? { ...n, startTick: current.startTick, pitch: current.pitch } : n)),
          );
        }
        return null;
      });
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }, [clip, onChangeNotes]);

  return (
    <div className="flex h-[128px] flex-none border-t border-shell-border bg-shell-bg-deep">
      <div className="flex w-[54px] flex-none flex-col overflow-hidden border-r border-shell-border font-mono">
        {rows.map((p) => (
          <div
            key={p}
            className={`flex items-center border-b border-shell-border px-[5px] py-[1px] text-[8px] text-shell-text-tertiary ${isBlackKey(p) ? "bg-black/[0.18]" : ""}`}
            style={{ height: ROW_HEIGHT }}
          >
            {pitchLabel(p)}
          </div>
        ))}
      </div>

      <div className="relative flex-1 overflow-auto">
        {!clip ? (
          <div className="flex h-full items-center justify-center text-[11.5px] text-shell-text-tertiary">
            Select or add a clip to edit notes
          </div>
        ) : (
          <div
            ref={gridRef}
            role="grid"
            aria-label={`Piano roll for ${clip.name}`}
            onClick={handleGridClick}
            className="relative cursor-pointer"
            style={{
              width: totalCols * COL_WIDTH,
              height: rows.length * ROW_HEIGHT,
              background:
                "linear-gradient(90deg, var(--color-shell-border, rgba(255,255,255,0.08)) 1px, transparent 1px) 0 0 / 36px 100%, linear-gradient(0deg, var(--color-shell-border, rgba(255,255,255,0.08)) 1px, transparent 1px) 0 0 / 100% 16px",
            }}
          >
            {clip.notes.map((n) => {
              const preview = dragPreview?.noteId === n.id ? dragPreview : null;
              const pitch = preview?.pitch ?? n.pitch;
              const startTick = preview?.startTick ?? n.startTick;
              const left = (startTick / SIXTEENTH) * COL_WIDTH;
              const top = (HIGHEST_PITCH - pitch) * ROW_HEIGHT + 1.5;
              const width = Math.max(COL_WIDTH - 2, (n.durationTicks / SIXTEENTH) * COL_WIDTH - 2);
              return (
                <span
                  key={n.id}
                  role="button"
                  aria-label={`Note ${pitchLabel(pitch)}`}
                  onPointerDown={(e) => handleNotePointerDown(e, n)}
                  onClick={(e) => e.stopPropagation()}
                  className="absolute rounded-[3px]"
                  style={{
                    left,
                    top,
                    width,
                    height: 13,
                    background: color,
                    boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
                    cursor: "grab",
                  }}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
