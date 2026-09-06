import { useEffect, useRef, useState } from "react";

export interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  labelledBy?: string;
  dragHandle?: boolean;
}

const DISMISS_THRESHOLD_PX = 80;

export function BottomSheet({
  open, onClose, children, labelledBy, dragHandle = true,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const [dragY, setDragY] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const first = sheetRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    first?.focus();
    return () => {
      previouslyFocused?.focus?.();
    };
  }, [open]);

  // Tab-cycling focus trap: keep focus inside the sheet while open.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const root = sheetRef.current;
      if (!root) return;
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )
      ).filter((el) => !el.hasAttribute("disabled"));
      if (focusables.length === 0) return;
      const first = focusables[0]!;
      const last = focusables[focusables.length - 1]!;
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) return null;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const startY = e.clientY;
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const onMove = (ev: PointerEvent) => {
      const dy = Math.max(0, ev.clientY - startY);
      setDragY(dy);
    };
    const onUp = (ev: PointerEvent) => {
      const dy = Math.max(0, ev.clientY - startY);
      el.releasePointerCapture(e.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      if (dy > DISMISS_THRESHOLD_PX) onClose();
      setDragY(0);
    };
    const onCancel = () => {
      el.releasePointerCapture(e.pointerId);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      setDragY(0);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
  };

  return (
    <>
      <div
        data-testid="bottom-sheet-backdrop"
        className="fixed inset-0 z-50 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className="fixed bottom-0 inset-x-0 z-50 bg-shell-surface rounded-t-xl border-t border-white/10 shadow-2xl max-h-[85vh] overflow-y-auto"
        style={{
          paddingBottom: "env(safe-area-inset-bottom, 0px)",
          transform: `translateY(${dragY}px)`,
          transition: dragY === 0 ? "transform 0.2s ease-out" : "none",
        }}
      >
        {dragHandle && (
          <div
            data-testid="bottom-sheet-handle"
            onPointerDown={onPointerDown}
            className="flex justify-center py-2 cursor-grab active:cursor-grabbing touch-none"
          >
            <div className="w-10 h-1 bg-white/20 rounded-full" />
          </div>
        )}
        {children}
      </div>
    </>
  );
}
