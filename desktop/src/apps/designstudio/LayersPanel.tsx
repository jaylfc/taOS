import { Eye, EyeOff, ChevronUp, ChevronDown, Trash2, Type, Square, Circle, Image as ImageIcon, Minus } from "lucide-react";
import type { CanvasElement } from "./types";
import { hasFill } from "./types";

const TYPE_ICON: Record<CanvasElement["type"], typeof Type> = {
  text: Type,
  rect: Square,
  ellipse: Circle,
  image: ImageIcon,
  line: Minus,
};

function layerLabel(el: CanvasElement): string {
  switch (el.type) {
    case "text":
      return el.text.trim().slice(0, 24) || "Text";
    case "image":
      return el.prompt?.slice(0, 24) || "Image";
    case "rect":
      return "Rectangle";
    case "ellipse":
      return "Circle";
    case "line":
      return "Line";
  }
}

function swatchColor(el: CanvasElement): string {
  if (hasFill(el)) return el.fill;
  if (el.type === "line") return el.stroke;
  return "#8b92a3";
}

export interface LayersPanelProps {
  elements: CanvasElement[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onToggleVisibility: (id: string) => void;
  onReorder: (id: string, direction: "up" | "down") => void;
  onDelete: (id: string) => void;
}

export function LayersPanel({
  elements,
  selectedId,
  onSelect,
  onToggleVisibility,
  onReorder,
  onDelete,
}: LayersPanelProps) {
  const byZDesc = [...elements].sort((a, b) => b.zIndex - a.zIndex);

  if (elements.length === 0) {
    return (
      <p className="px-0.5 text-[11.5px] leading-[1.4] text-shell-text-tertiary">
        No layers yet. Add an element to see it here.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-1">
      {byZDesc.map((el, i) => {
        const Icon = TYPE_ICON[el.type];
        const isSelected = el.id === selectedId;
        return (
          <li key={el.id}>
            <div
              className={`group flex items-center gap-1.5 rounded-[9px] border px-2 py-1.5 text-[11.5px] transition-colors ${
                isSelected
                  ? "border-accent/50 bg-accent/15 text-shell-text"
                  : "border-transparent text-shell-text-secondary hover:bg-shell-surface-active"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(el.id)}
                aria-label={`Select layer ${layerLabel(el)}`}
                aria-pressed={isSelected}
                className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
              >
                <span
                  className="h-2.5 w-2.5 flex-none rounded-full border border-white/20"
                  style={{ background: swatchColor(el) }}
                  aria-hidden="true"
                />
                <Icon size={13} className="flex-none" />
                <span className="truncate">{layerLabel(el)}</span>
              </button>

              <button
                type="button"
                onClick={() => onReorder(el.id, "up")}
                disabled={i === 0}
                aria-label={`Move ${layerLabel(el)} up`}
                className="flex-none rounded p-0.5 text-shell-text-tertiary hover:text-shell-text disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <ChevronUp size={13} />
              </button>
              <button
                type="button"
                onClick={() => onReorder(el.id, "down")}
                disabled={i === byZDesc.length - 1}
                aria-label={`Move ${layerLabel(el)} down`}
                className="flex-none rounded p-0.5 text-shell-text-tertiary hover:text-shell-text disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <ChevronDown size={13} />
              </button>
              <button
                type="button"
                onClick={() => onToggleVisibility(el.id)}
                aria-label={el.visible ? `Hide ${layerLabel(el)}` : `Show ${layerLabel(el)}`}
                aria-pressed={!el.visible}
                className="flex-none rounded p-0.5 text-shell-text-tertiary hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                {el.visible ? <Eye size={13} /> : <EyeOff size={13} />}
              </button>
              <button
                type="button"
                onClick={() => onDelete(el.id)}
                aria-label={`Delete ${layerLabel(el)}`}
                className="flex-none rounded p-0.5 text-shell-text-tertiary hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Trash2 size={13} />
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
