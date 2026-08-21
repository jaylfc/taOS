import { useState } from "react";
import { createPortal } from "react-dom";
import { projectsApi } from "@/lib/projects";

interface CreateListDialogProps {
  projectId: string;
  onClose: () => void;
  onCreated: (listId: string) => void;
}

export function CreateListDialog({ projectId, onClose, onCreated }: CreateListDialogProps) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await projectsApi.lists.create(projectId, { title: trimmed });
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="New list"
      className="fixed inset-0 z-[10001] bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <form
        onSubmit={onSubmit}
        className="bg-zinc-900 p-4 rounded shadow w-full max-w-sm space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">New list</h3>
        <label className="block text-sm text-zinc-400">
          Name
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            type="text"
            autoFocus
            required
            className="w-full mt-1 px-2 py-1 bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded outline-none focus:ring-2 focus:ring-zinc-600"
          />
        </label>
        {error && <div role="alert" className="text-sm text-red-400">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="px-3 py-1 bg-blue-600 rounded text-sm font-medium text-white disabled:opacity-50">
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
