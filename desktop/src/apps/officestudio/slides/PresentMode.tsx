import { ChevronLeft, ChevronRight, LogOut } from "lucide-react";
import { SlideCanvas } from "./SlideCanvas";
import type { Deck } from "./deck";

// The fullscreen presentation surface. HARD REQUIREMENT (matching Game
// Studio's PlayView): an always-visible "Exit" control is layered on top
// whenever this is showing, and Escape exits (wired by the caller), so the
// user is never trapped.

export interface PresentModeProps {
  deck: Deck;
  index: number;
  onNext: () => void;
  onPrev: () => void;
  onExit: () => void;
}

export function PresentMode({ deck, index, onNext, onPrev, onExit }: PresentModeProps) {
  const slide = deck.slides[index];
  const total = deck.slides.length;
  if (!slide) return null;

  return (
    <div
      className="relative flex h-full w-full items-center justify-center bg-black"
      role="dialog"
      aria-label={`Presenting ${deck.title || "deck"}`}
    >
      <div className="w-full" style={{ maxWidth: "min(92vw, 163.5vh)" }}>
        <SlideCanvas slide={slide} className="w-full" />
      </div>

      {/* persistent Exit control, never hidden while presenting */}
      <button
        type="button"
        onClick={onExit}
        aria-label="Exit presentation"
        className="absolute z-50 inline-flex items-center gap-1.5 rounded-full border border-accent/45 bg-[rgba(16,18,24,0.62)] px-3 py-1.5 text-[12px] font-bold text-white shadow-lg backdrop-blur-md transition-all hover:-translate-y-0.5 hover:bg-[rgba(28,32,42,0.82)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        style={{
          top: "max(12px, env(safe-area-inset-top, 12px))",
          left: "max(12px, env(safe-area-inset-left, 12px))",
        }}
      >
        <LogOut size={15} className="-scale-x-100" />
        Exit
        <span className="ml-0.5 rounded border border-white/25 px-1.5 py-px text-[9.5px] font-bold tracking-wide text-white/60">
          ESC
        </span>
      </button>

      <div className="absolute bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-full bg-black/50 px-3 py-1 text-[12px] font-semibold text-white/80 backdrop-blur-sm">
        {index + 1} / {total}
      </div>

      <button
        type="button"
        onClick={onPrev}
        disabled={index === 0}
        aria-label="Previous slide"
        className="absolute left-4 top-1/2 z-50 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/45 text-white backdrop-blur-sm transition-colors hover:bg-black/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronLeft size={20} />
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={index === total - 1}
        aria-label="Next slide"
        className="absolute right-4 top-1/2 z-50 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/45 text-white backdrop-blur-sm transition-colors hover:bg-black/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronRight size={20} />
      </button>
    </div>
  );
}
