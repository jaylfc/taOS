import { useEffect, useRef } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as destructive (red) when the action removes/deletes. */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A small modal that asks the user to confirm before a destructive or
 * hard-to-undo action. Matches the app dialog style (overlay + zinc panel),
 * focuses the confirm button, closes on Escape or backdrop click, and keeps
 * keyboard focus inside the panel (Tab cycles within the dialog).
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) confirmRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      // Trap Tab focus inside the panel so it cannot reach controls behind the
      // modal.
      const focusable = panelRef.current.querySelectorAll<HTMLElement>("button");
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      } else if (!panelRef.current.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-[10002] bg-black/50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        ref={panelRef}
        className="bg-zinc-900 p-4 rounded shadow w-full max-w-sm space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">{title}</h3>
        {message && <p className="text-sm text-zinc-400">{message}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onCancel}
            className="text-xs px-3 py-1.5 bg-zinc-800 rounded hover:bg-zinc-700"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            className={
              danger
                ? "text-xs px-3 py-1.5 bg-red-600 rounded hover:bg-red-500 text-white"
                : "text-xs px-3 py-1.5 bg-blue-600 rounded hover:bg-blue-500 text-white"
            }
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
