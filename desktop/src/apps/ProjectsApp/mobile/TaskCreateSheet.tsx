import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface TaskCreatePayload {
  title: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (payload: TaskCreatePayload) => Promise<void>;
}

export function TaskCreateSheet({ open, onClose, onSubmit }: Props) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setTitle("");
      setError(null);
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  if (!open) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({ title: title.trim() });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="New task"
      data-testid="task-create-sheet"
      className="fixed inset-0 z-50 flex items-end"
    >
      <button
        type="button"
        aria-label="Dismiss"
        className="absolute inset-0 bg-black/50 disabled:cursor-not-allowed"
        onClick={onClose}
        disabled={submitting}
      />
      <form
        onSubmit={submit}
        className="relative z-10 w-full rounded-t-2xl bg-zinc-900 p-4 shadow-xl border-t border-zinc-800"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 1rem)" }}
      >
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-zinc-700" />
        <h2 className="mb-3 text-base font-semibold">New task</h2>
        <input
          ref={inputRef}
          type="text"
          aria-label="Task title"
          placeholder="Task title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-lg bg-zinc-800 px-3 py-3 text-base outline-none focus:ring-2 focus:ring-zinc-600"
          disabled={submitting}
        />
        {error && (
          <div role="alert" className="mt-2 text-sm text-red-400">
            {error}
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-zinc-400 disabled:opacity-50"
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            disabled={!title.trim() || submitting}
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
