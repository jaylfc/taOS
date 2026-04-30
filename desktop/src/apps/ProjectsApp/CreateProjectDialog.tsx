import { useState } from "react";
import { createPortal } from "react-dom";
import { projectsApi } from "@/lib/projects";

const slugify = (s: string) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function CreateProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Name is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await projectsApi.create({
        name: trimmedName,
        slug: slug.trim() || slugify(trimmedName),
        description: description.trim(),
      });
      onCreated();
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
      aria-label="Create project"
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
    >
      <form
        onSubmit={onSubmit}
        className="bg-zinc-900 p-4 rounded shadow w-full max-w-sm space-y-3"
      >
        <h3 className="text-lg font-semibold">New Project</h3>
        <label className="block text-sm">
          Name
          <input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slug) setSlug(slugify(e.target.value));
            }}
            required
            autoFocus
            className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
          />
        </label>
        <label className="block text-sm">
          Slug
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            pattern="[a-z0-9-]+"
            required
            className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
          />
        </label>
        <label className="block text-sm">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
          />
        </label>
        {error && <div role="alert" className="text-red-400 text-xs">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-3 py-1 bg-blue-600 rounded text-sm disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
