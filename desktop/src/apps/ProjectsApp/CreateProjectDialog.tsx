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
  const [slugTouched, setSlugTouched] = useState(false);
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
        className="bg-zinc-900 text-zinc-200 p-4 rounded shadow w-full max-w-sm space-y-3"
      >
        <h3 className="text-lg font-semibold text-zinc-200">New Project</h3>
        <label className="block text-sm text-zinc-400">
          Name
          <input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slugTouched) setSlug(slugify(e.target.value));
            }}
            required
            autoFocus
            className="w-full mt-1 px-2 py-1 bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded outline-none focus:ring-2 focus:ring-zinc-600"
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Slug
          <input
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setSlugTouched(true);
            }}
            pattern="[a-z0-9-]+"
            required
            className="w-full mt-1 px-2 py-1 bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded outline-none focus:ring-2 focus:ring-zinc-600"
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full mt-1 px-2 py-1 bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded outline-none focus:ring-2 focus:ring-zinc-600"
          />
        </label>
        {error && <div role="alert" className="mt-2 text-sm text-red-400">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
