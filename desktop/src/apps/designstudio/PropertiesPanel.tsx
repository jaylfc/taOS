import type { ReactNode } from "react";
import { AlignLeft, AlignCenter, AlignRight, Copy, Trash2, Sparkles } from "lucide-react";
import type { CanvasElement } from "./types";

const MAGIC_CHIPS = ["Make it bolder", "Match brand", "Rewrite copy"];

export interface PropertiesPanelProps {
  selected: CanvasElement | null;
  onUpdate: (patch: Partial<CanvasElement>) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="mb-2 block text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
        {label}
      </label>
      {children}
    </div>
  );
}

export function PropertiesPanel({ selected, onUpdate, onDuplicate, onDelete }: PropertiesPanelProps) {
  if (!selected) {
    return (
      <p className="px-0.5 text-[11.5px] leading-[1.4] text-shell-text-tertiary">
        Select an element to edit its properties.
      </p>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
          Selection
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={onDuplicate}
            aria-label="Duplicate element"
            title="Duplicate (Cmd/Ctrl+D)"
            className="rounded-[8px] border border-shell-border bg-shell-surface p-1.5 text-shell-text-secondary transition-colors hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Copy size={14} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label="Delete element"
            title="Delete"
            className="rounded-[8px] border border-shell-border bg-shell-surface p-1.5 text-shell-text-secondary transition-colors hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {selected.type === "text" && (
        <>
          <Field label="Text">
            <div className="flex gap-2">
              <input
                aria-label="Font size"
                type="number"
                min={8}
                max={400}
                value={selected.fontSize}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  if (!Number.isNaN(n)) onUpdate({ fontSize: n });
                }}
                className="w-[62px] rounded-[10px] border border-shell-border bg-shell-surface py-[9px] text-center text-[12.5px] font-semibold font-mono text-shell-text outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              />
              <input
                aria-label="Text color"
                type="color"
                value={selected.fill}
                onChange={(e) => onUpdate({ fill: e.target.value })}
                className="h-[36px] flex-1 cursor-pointer rounded-[10px] border border-shell-border bg-shell-surface"
              />
            </div>
          </Field>
          <Field label="Align">
            <div className="flex gap-[7px]">
              {(
                [
                  ["left", AlignLeft],
                  ["center", AlignCenter],
                  ["right", AlignRight],
                ] as const
              ).map(([align, Icon]) => (
                <button
                  key={align}
                  type="button"
                  aria-label={`Align ${align}`}
                  aria-pressed={selected.align === align}
                  onClick={() => onUpdate({ align })}
                  className={`flex h-[34px] flex-1 items-center justify-center rounded-[9px] border text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    selected.align === align
                      ? "border-accent/50 bg-shell-surface-active text-shell-text"
                      : "border-shell-border bg-shell-surface text-shell-text-secondary hover:text-shell-text"
                  }`}
                >
                  <Icon size={15} />
                </button>
              ))}
            </div>
          </Field>
        </>
      )}

      {(selected.type === "rect" || selected.type === "ellipse") && (
        <Field label="Fill & stroke">
          <div className="flex gap-2">
            <input
              aria-label="Fill color"
              type="color"
              value={selected.fill}
              onChange={(e) => onUpdate({ fill: e.target.value })}
              className="h-[36px] flex-1 cursor-pointer rounded-[10px] border border-shell-border bg-shell-surface"
            />
            <input
              aria-label="Stroke color"
              type="color"
              value={selected.stroke}
              onChange={(e) => onUpdate({ stroke: e.target.value })}
              className="h-[36px] flex-1 cursor-pointer rounded-[10px] border border-shell-border bg-shell-surface"
            />
            <input
              aria-label="Stroke width"
              type="number"
              min={0}
              max={40}
              value={selected.strokeWidth}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!Number.isNaN(n)) onUpdate({ strokeWidth: n });
              }}
              className="w-[54px] rounded-[10px] border border-shell-border bg-shell-surface py-[9px] text-center text-[12.5px] font-semibold font-mono text-shell-text outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            />
          </div>
        </Field>
      )}

      {selected.type === "line" && (
        <Field label="Stroke">
          <div className="flex gap-2">
            <input
              aria-label="Stroke color"
              type="color"
              value={selected.stroke}
              onChange={(e) => onUpdate({ stroke: e.target.value })}
              className="h-[36px] flex-1 cursor-pointer rounded-[10px] border border-shell-border bg-shell-surface"
            />
            <input
              aria-label="Stroke width"
              type="number"
              min={1}
              max={40}
              value={selected.strokeWidth}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!Number.isNaN(n)) onUpdate({ strokeWidth: n });
              }}
              className="w-[54px] rounded-[10px] border border-shell-border bg-shell-surface py-[9px] text-center text-[12.5px] font-semibold font-mono text-shell-text outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            />
          </div>
        </Field>
      )}

      <Field label="Position & size">
        <div className="grid grid-cols-2 gap-2 text-[11.5px] font-mono text-shell-text-secondary">
          <div className="rounded-[9px] border border-shell-border bg-shell-surface px-2 py-1.5">
            X {Math.round(selected.x)}
          </div>
          <div className="rounded-[9px] border border-shell-border bg-shell-surface px-2 py-1.5">
            Y {Math.round(selected.y)}
          </div>
          <div className="rounded-[9px] border border-shell-border bg-shell-surface px-2 py-1.5">
            W {Math.round(selected.width)}
          </div>
          <div className="rounded-[9px] border border-shell-border bg-shell-surface px-2 py-1.5">
            H {Math.round(selected.height)}
          </div>
        </div>
      </Field>

      <div
        className="rounded-[14px] p-[13px]"
        style={{
          border: "1px solid rgba(139,146,163,0.35)",
          background:
            "radial-gradient(120% 130% at 12% 10%, rgba(139,146,163,0.35), transparent 60%), var(--color-shell-surface, rgba(255,255,255,0.045))",
        }}
      >
        <div className="flex items-center gap-[7px] text-[12.5px] font-bold">
          <Sparkles size={15} className="text-shell-text-secondary" />
          Magic edits
        </div>
        <p className="mt-1.5 text-[11.5px] leading-[1.45] text-shell-text-secondary">
          Coming soon: ask for a change in words and taOS restyles the selected layer.
        </p>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {MAGIC_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              disabled
              title="Coming soon"
              className="cursor-not-allowed rounded-full bg-shell-surface-active px-[10px] py-[5px] text-[11px] font-semibold text-shell-text-tertiary opacity-60"
            >
              {chip}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
