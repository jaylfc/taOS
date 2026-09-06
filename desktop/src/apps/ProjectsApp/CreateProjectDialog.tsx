import { useState } from "react";
import { createPortal } from "react-dom";
import { projectsApi, type ElementType } from "@/lib/projects";
import { ELEMENT_TYPES, ELEMENT_TYPE_ORDER } from "./elements/types";

const slugify = (s: string) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

interface ElementDraft {
  name: string;
  type: string;
}

export function CreateProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [elements, setElements] = useState<ElementDraft[]>([{ name: "", type: "generic" }]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createProject = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Name is required.");
      return null;
    }
    return projectsApi.create({
      name: trimmedName,
      slug: slug.trim() || slugify(trimmedName),
      description: description.trim(),
    });
  };

  // Step 1 submit: create the project only (skip elements). Enter-to-submit and
  // the primary "Create" button both take this path, so a project with no
  // elements is byte-for-byte today's behaviour.
  const onSubmitStep1 = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await createProject();
      onCreated();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  // Step 2 submit: create the project, then each named element in parallel.
  const onSubmitStep2 = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    const named = elements.filter((el) => el.name.trim());
    setSubmitting(true);
    setError(null);
    try {
      const project = await createProject();
      if (!project) return;
      await Promise.all(
        named.map((el) =>
          projectsApi.elements.create(project.id, {
            name: el.name.trim(),
            slug: slugify(el.name.trim()),
            type: (el.type || "generic") as ElementType,
          }),
        ),
      );
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
      className="fixed inset-0 z-[10001] bg-black/50 flex items-center justify-center p-4"
    >
      <form
        onSubmit={step === 1 ? onSubmitStep1 : onSubmitStep2}
        className="bg-zinc-900 text-zinc-200 p-4 rounded shadow w-full max-w-sm space-y-3"
      >
        <h3 className="text-lg font-semibold text-zinc-200">
          {step === 1 ? "New Project" : "Add elements"}
        </h3>

        {step === 1 && (
          <>
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
          </>
        )}

        {step === 2 && (
          <div className="space-y-2">
            <p className="text-xs text-zinc-500">
              Optional. Give the project a few nested elements. Leave blank to skip.
            </p>
            {elements.map((el, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input
                  value={el.name}
                  onChange={(e) => {
                    const next = [...elements];
                    next[i] = { ...next[i]!, name: e.target.value };
                    setElements(next);
                  }}
                  placeholder="Element name"
                  aria-label={`Element ${i + 1} name`}
                  className="flex-1 min-w-0 px-2 py-1 bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded outline-none focus:ring-2 focus:ring-zinc-600"
                />
                <select
                  value={el.type}
                  onChange={(e) => {
                    const next = [...elements];
                    next[i] = { ...next[i]!, type: e.target.value };
                    setElements(next);
                  }}
                  aria-label={`Element ${i + 1} type`}
                  className="px-2 py-1 bg-zinc-800 text-zinc-100 rounded outline-none focus:ring-2 focus:ring-zinc-600"
                >
                  {ELEMENT_TYPE_ORDER.map((t) => (
                    <option key={t} value={t}>
                      {ELEMENT_TYPES[t]!.label}
                      {t === "generic" ? " (default)" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  aria-label={`Remove element ${i + 1}`}
                  onClick={() => setElements(elements.filter((_, j) => j !== i))}
                  className="px-2 py-1 text-zinc-400 hover:text-zinc-200"
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setElements([...elements, { name: "", type: "generic" }])}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              + Add another element
            </button>
          </div>
        )}

        {error && <div role="alert" className="mt-2 text-sm text-red-400">{error}</div>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50">
            Cancel
          </button>
          {step === 1 ? (
            <>
              <button
                type="button"
                onClick={() => setStep(2)}
                className="px-3 py-1 text-sm text-blue-300 hover:text-blue-200"
              >
                Add elements
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white disabled:opacity-50"
              >
                {submitting ? "Creating…" : "Create"}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setStep(1)}
                disabled={submitting}
                className="px-3 py-1 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white disabled:opacity-50"
              >
                {submitting ? "Creating…" : "Create project & elements"}
              </button>
            </>
          )}
        </div>
      </form>
    </div>,
    document.body,
  );
}
