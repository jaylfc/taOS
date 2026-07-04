import { useCallback, useEffect, useRef, useState } from "react";
import { TICKS_PER_BEAT, localId, type Clip, type Note } from "./types";

/* ------------------------------------------------------------------ */
/*  PianoRoll                                                           */
/*                                                                     */
/*  Interaction (non-destructive on a single click):                    */
/*   - click an empty grid cell  -> ADD a note (and select it)          */
/*   - click an existing note    -> SELECT it (never deletes)           */
/*   - drag a selected note      -> MOVE it (past a small pixel          */
/*                                  threshold, so a jittery click is a   */
/*                                  select, not a move)                  */
/*   - Delete / Backspace        -> delete the selected note            */
/*                                                                     */
/*  Grid: 36px per sixteenth note, 16px per semitone row.               */
/* ------------------------------------------------------------------ */

const ROW_HEIGHT = 16;
const COL_WIDTH = 36;
const SIXTEENTH = TICKS_PER_BEAT / 4;
const LOWEST_PITCH = 36; // C2
const HIGHEST_PITCH = 96; // C7
/** Pointer must move at least this many px before a note drag mutates the
 *  model; below it the gesture is treated as a plain click (select only). */
const DRAG_THRESHOLD_PX = 4;

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
  const dragState = useRef<{ noteId: string; startX: number; startY: number; origTick: number; origPitch: number; moved: boolean } | null>(null);
  const [dragPreview, setDragPreview] = useState<{ noteId: string; startTick: number; pitch: number } | null>(null);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);

  // The pointer handlers below are registered once per gesture but may outlive
  // a change of the selected clip -- read the live clip through a ref so a
  // mid-drag clip switch never mutates the wrong clip's notes.
  const clipRef = useRef(clip);
  clipRef.current = clip;
  const onChangeRef = useRef(onChangeNotes);
  onChangeRef.current = onChangeNotes;

  const lengthTicks = (clip?.lengthBars ?? 1) * 4 * TICKS_PER_BEAT;
  const totalCols = Math.max(1, Math.round(lengthTicks / SIXTEENTH));

  const handleGridClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const live = clipRef.current;
      if (!live || dragState.current) return;
      const rect = gridRef.current!.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const col = Math.floor(x / COL_WIDTH);
      const rowIndex = Math.floor(y / ROW_HEIGHT);
      const pitch = HIGHEST_PITCH - rowIndex;
      const startTick = col * SIXTEENTH;
      if (pitch < LOWEST_PITCH || pitch > HIGHEST_PITCH || col < 0 || col >= totalCols) return;

      const existing = live.notes.find((n) => n.pitch === pitch && n.startTick === startTick);
      if (existing) {
        // Click on an existing note: SELECT it (deletion is explicit -- see
        // the Delete/Backspace handler). Never destructive on a click.
        setSelectedNoteId(existing.id);
        return;
      }
      const newNote: Note = { id: localId("note"), pitch, startTick, durationTicks: SIXTEENTH, velocity: 0.85 };
      setSelectedNoteId(newNote.id);
      onChangeRef.current([...live.notes, newNote]);
    },
    [totalCols],
  );

  const handleNotePointerDown = useCallback((e: React.PointerEvent, note: Note) => {
    e.stopPropagation();
    setSelectedNoteId(note.id);
    dragState.current = { noteId: note.id, startX: e.clientX, startY: e.clientY, origTick: note.startTick, origPitch: note.pitch, moved: false };
    setDragPreview({ noteId: note.id, startTick: note.startTick, pitch: note.pitch });

    const handleMove = (ev: PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const dx = ev.clientX - drag.startX;
      const dy = ev.clientY - drag.startY;
      if (!drag.moved && Math.abs(dx) < DRAG_THRESHOLD_PX && Math.abs(dy) < DRAG_THRESHOLD_PX) return;
      drag.moved = true;
      const deltaCols = Math.round(dx / COL_WIDTH);
      const deltaRows = Math.round(dy / ROW_HEIGHT);
      const nextTick = Math.max(0, drag.origTick + deltaCols * SIXTEENTH);
      const nextPitch = Math.max(LOWEST_PITCH, Math.min(HIGHEST_PITCH, drag.origPitch - deltaRows));
      setDragPreview({ noteId: drag.noteId, startTick: nextTick, pitch: nextPitch });
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      const drag = dragState.current;
      dragState.current = null;
      const live = clipRef.current;
      // Only a real drag (past the threshold) mutates; a plain click just
      // leaves the note selected. No deletion happens here.
      if (live && drag && drag.moved) {
        setDragPreview((current) => {
          if (current && current.noteId === drag.noteId) {
            onChangeRef.current(
              live.notes.map((n) => (n.id === drag.noteId ? { ...n, startTick: current.startTick, pitch: current.pitch } : n)),
            );
          }
          return null;
        });
      } else {
        setDragPreview(null);
      }
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }, []);

  const deleteSelected = useCallback(() => {
    const live = clipRef.current;
    if (!live || !selectedNoteId) return;
    onChangeRef.current(live.notes.filter((n) => n.id !== selectedNoteId));
    setSelectedNoteId(null);
  }, [selectedNoteId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        deleteSelected();
      }
    },
    [deleteSelected],
  );

  // Clear the selection when the clip changes so a stale id can't be deleted
  // against a different clip.
  useEffect(() => {
    setSelectedNoteId(null);
  }, [clip?.id]);

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
            tabIndex={0}
            aria-label={`Piano roll for ${clip.name}`}
            onClick={handleGridClick}
            onKeyDown={handleKeyDown}
            className="relative cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
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
              const selected = n.id === selectedNoteId;
              return (
                <span
                  key={n.id}
                  role="button"
                  aria-label={`Note ${pitchLabel(pitch)}`}
                  aria-pressed={selected}
                  onPointerDown={(e) => handleNotePointerDown(e, n)}
                  onClick={(e) => e.stopPropagation()}
                  className={`absolute rounded-[3px] ${selected ? "outline outline-2 outline-white" : ""}`}
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
