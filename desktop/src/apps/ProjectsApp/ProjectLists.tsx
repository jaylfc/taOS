import { useEffect, useState, useRef, useCallback } from "react";
import { projectsApi, type Project, type ProjectList, type ProjectListEntry } from "@/lib/projects";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CreateListDialog } from "./CreateListDialog";
import styles from "./ProjectsApp.module.css";

const STATUS_STYLE: Record<string, string> = {
  new: "bg-blue-500/15 text-blue-400",
  seen: "bg-shell-bg-deep text-shell-text-tertiary",
  actioned: "bg-green-500/15 text-green-400",
  discuss: "bg-amber-500/15 text-amber-400",
};

const EMPTY_LIST: ProjectList = { id: "", project_id: "", title: "", description: "", status: "active", created_by: "", created_at: 0, updated_at: 0 };

export function ProjectLists({ project }: { project: Project }) {
  const [lists, setLists] = useState<ProjectList[]>([]);
  const [selectedListId, setSelectedListId] = useState<string | null>(null);
  const [entries, setEntries] = useState<ProjectListEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quickText, setQuickText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [removeConfirmEntry, setRemoveConfirmEntry] = useState<{ listId: string; entryId: string } | null>(null);
  const [showOriginalId, setShowOriginalId] = useState<string | null>(null);
  const quickInputRef = useRef<HTMLInputElement>(null);
  // Current selection, readable from inside an async closure that captured an
  // older one. Assigned during render so it is correct before any effect runs.
  const selectedListIdRef = useRef<string | null>(selectedListId);
  selectedListIdRef.current = selectedListId;

  // Drop the previous project's selection DURING RENDER, not in an effect.
  // ProjectWorkspace renders <ProjectLists project={project} /> unkeyed and is
  // itself unkeyed, so a project switch reuses this component. Resetting in an
  // effect is too late: the entries effect re-runs in the same commit (its deps
  // include project.id) and would fetch
  // /api/projects/<new>/lists/<old-list-id>/entries first. This is React's
  // adjust-state-on-prop-change pattern and it runs before any effect.
  const [prevProjectId, setPrevProjectId] = useState(project.id);
  if (prevProjectId !== project.id) {
    setPrevProjectId(project.id);
    setSelectedListId(null);
    setEntries([]);
  }

  const refreshLists = useCallback(() => {
    // Sets state, like refreshEntries below. It used to only RETURN the lists,
    // so createList/deleteList awaited a fetch whose result was discarded -- a
    // created list never appeared in the rail and a deleted one never left it,
    // because the mount effect was the only caller of setLists.
    return projectsApi.lists.list(project.id)
      .then((ls) => {
        setLists(ls);
        return ls;
      })
      .catch(() => {
        setError("Could not load lists.");
        return [] as ProjectList[];
      });
  }, [project.id]);

  const refreshEntries = useCallback(() => {
    if (!selectedListId) return Promise.resolve([] as ProjectListEntry[]);
    const forListId = selectedListId;
    return projectsApi.lists.entries.list(project.id, forListId)
      .then((ents) => {
        // Drop a response whose list is no longer selected. Two quick clicks
        // race, and the slower fetch used to land last and show one list's
        // entries under another's heading. The ref is the CURRENT selection;
        // this closure's own copy is stale by definition.
        if (forListId !== selectedListIdRef.current) return ents;
        setEntries(ents);
        return ents;
      })
      .catch(() => {
        setError("Could not load entries.");
        return [] as ProjectListEntry[];
      });
  }, [project.id, selectedListId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    refreshLists()
      .then((ls) => {
        if (!cancelled) {
          setLists(ls);
          // Keep the selection only if it belongs to THIS project's lists.
          // ProjectWorkspace renders <ProjectLists project={project} /> with no
          // key and is itself unkeyed, so switching project does not remount:
          // the previous project's selectedListId survived and the entries
          // effect then fetched /api/projects/<new>/lists/<old-id>/entries.
          // The functional form also avoids depending on a stale closure value.
          setSelectedListId((prev) =>
            prev && ls.some((l) => l.id === prev) ? prev : (ls[0]?.id ?? null));
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load lists for this project.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [project.id, refreshLists]);

  useEffect(() => {
    if (!selectedListId) { setEntries([]); return; }
    let cancelled = false;
    setLoading(true);
    refreshEntries().finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedListId, refreshEntries]);

  const handleCreatedList = useCallback((listId: string) => {
    setCreateDialogOpen(false);
    setSelectedListId(listId);
    refreshLists();
  }, [refreshLists]);

  const confirmDeleteList = useCallback(async () => {
    if (!deleteConfirmId) return;
    const listId = deleteConfirmId;
    setDeleteConfirmId(null);
    try {
      await projectsApi.lists.remove(project.id, listId);
      if (selectedListId === listId) setSelectedListId(null);
      await refreshLists();
    } catch (err) {
      setError(String(err));
    }
  }, [deleteConfirmId, project.id, refreshLists, selectedListId]);

  const confirmRemoveEntry = useCallback(async () => {
    if (!removeConfirmEntry) return;
    const { listId, entryId } = removeConfirmEntry;
    setRemoveConfirmEntry(null);
    try {
      await projectsApi.lists.entries.remove(project.id, listId, entryId);
      await refreshEntries();
    } catch (err) {
      setError(String(err));
    }
  }, [removeConfirmEntry, project.id, refreshEntries]);

  const createEntry = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = quickText.trim();
    if (!text || submitting || !selectedListId) return;
    setSubmitting(true);
    try {
      await projectsApi.lists.entries.create(project.id, selectedListId, { text });
      setQuickText("");
      await refreshEntries();
      quickInputRef.current?.focus();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const toggleDone = async (entry: ProjectListEntry) => {
    try {
      await projectsApi.lists.entries.update(project.id, entry.list_id, entry.id, { done: entry.done ? 0 : 1 });
      await refreshEntries();
    } catch (err) {
      setError(String(err));
    }
  };

  const updateEntryStatus = async (entry: ProjectListEntry, status: string) => {
    try {
      await projectsApi.lists.entries.update(project.id, entry.list_id, entry.id, { status });
      await refreshEntries();
    } catch (err) {
      setError(String(err));
    }
  };

  const selectedList = lists.find((l) => l.id === selectedListId) ?? EMPTY_LIST;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className={`${styles.listsContainer} flex flex-1 min-h-0 gap-3`}>
        <aside className={styles.listsRail} aria-label="Lists">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-shell-text">Lists</h2>
            <button
              type="button"
              onClick={() => setCreateDialogOpen(true)}
              aria-label="Create new list"
              className="rounded-full bg-accent/10 px-2 py-1 text-xs font-medium text-accent hover:bg-accent/20"
            >
              + New
            </button>
          </div>
          {loading && lists.length === 0 ? (
            <p className="text-xs text-shell-text-tertiary">Loading…</p>
          ) : lists.length === 0 ? (
            <p className="text-xs text-shell-text-secondary">No lists yet.</p>
          ) : (
            <ul className="flex flex-col gap-1" role="listbox" aria-label="Project lists">
              {lists.map((lst) => (
                <li
                  key={lst.id}
                  role="option"
                  aria-selected={lst.id === selectedListId}
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedListId(lst.id); } }}
                  onClick={() => setSelectedListId(lst.id)}
                  className={`flex items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-sm cursor-pointer transition-colors ${lst.id === selectedListId ? "bg-shell-surface-active text-shell-text" : "text-shell-text-secondary hover:bg-shell-surface-hover hover:text-shell-text"}`}
                >
                  <span className="truncate">{lst.title || "Untitled list"}</span>
                  <button
                    type="button"
                    aria-label={`Delete ${lst.title || "Untitled list"}`}
                    onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(lst.id); }}
                    className="rounded p-0.5 text-shell-text-tertiary hover:text-red-400"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <main className={styles.listsEntriesPanel} aria-label="List entries">
          {!selectedListId ? (
            <p className="text-sm text-shell-text-secondary">Select or create a list.</p>
          ) : (
            <div className="flex flex-col gap-3 h-full">
              <header className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-shell-text truncate">{selectedList.title || "Untitled list"}</h3>
                {selectedList.description && (
                  <span className="text-xs text-shell-text-tertiary truncate" title={selectedList.description}>{selectedList.description}</span>
                )}
              </header>

              <form onSubmit={createEntry} className="flex gap-2">
                <label htmlFor={`quick-add-${selectedList.id}`} className="sr-only">Add entry to {selectedList.title || "Untitled list"}</label>
                <input
                  ref={quickInputRef}
                  id={`quick-add-${selectedList.id}`}
                  type="text"
                  value={quickText}
                  onChange={(e) => setQuickText(e.target.value)}
                  placeholder="Add entry…"
                  disabled={submitting}
                  aria-label={`Quick add entry to ${selectedList.title || "Untitled list"}`}
                  className="flex-1 rounded-lg border border-shell-border bg-shell-surface px-3 py-1.5 text-sm text-shell-text outline-none focus:ring-2 focus:ring-accent-line disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={submitting || !quickText.trim()}
                  aria-label="Add entry"
                  className="rounded-lg bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
                >
                  {submitting ? "Adding…" : "Add"}
                </button>
              </form>

              {error && (
                <p role="alert" className="text-xs text-red-400">{error}</p>
              )}

              {loading ? (
                <p className="text-sm text-shell-text-tertiary">Loading entries…</p>
              ) : entries.length === 0 ? (
                <p className="text-sm text-shell-text-secondary">No entries yet. Type above and press Enter to add one.</p>
              ) : (
                <ul className="flex flex-col gap-2" aria-label="Entries">
                  {entries.map((entry) => {
                    const tidied = entry.text !== entry.original_text;
                    return (
                      <li
                        key={entry.id}
                        className="flex flex-col gap-1.5 rounded-xl border border-shell-border bg-shell-surface p-3.5"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1.5 text-xs text-shell-text-secondary">
                            <input
                              type="checkbox"
                              checked={!!entry.done}
                              onChange={() => toggleDone(entry)}
                              aria-label={`Mark ${entry.text} as done`}
                              className="rounded border-shell-border"
                            />
                            Done
                          </label>
                          {entry.category && (
                            <span className="inline-flex items-center rounded-full bg-shell-border px-2 py-0.5 text-xs font-medium text-shell-text-secondary">
                              {entry.category}
                            </span>
                          )}
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_STYLE[entry.status] ?? "bg-shell-border text-shell-text-secondary"}`}
                          >
                            {entry.status}
                          </span>
                          {tidied && (
                            <span className="relative inline-flex">
                              <button
                                type="button"
                                className="inline-flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text"
                                title={entry.original_text}
                                onClick={() => setShowOriginalId(showOriginalId === entry.id ? null : entry.id)}
                                aria-label={`View original text for ${entry.text}`}
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                                  <circle cx="12" cy="12" r="3" />
                                </svg>
                                original
                              </button>
                              {showOriginalId === entry.id && (
                                <div className="absolute left-0 top-full mt-1 z-50 text-xs text-shell-text bg-shell-bg-deep border border-shell-border rounded-lg p-2.5 shadow-lg whitespace-pre-wrap max-w-[280px]" role="tooltip">
                                  {entry.original_text}
                                </div>
                              )}
                            </span>
                          )}
                          <div className="ml-auto flex gap-2 text-xs">
                            <select
                              value={entry.status}
                              onChange={(e) => updateEntryStatus(entry, e.target.value)}
                              aria-label={`Change status for ${entry.text}`}
                              className="rounded bg-shell-bg-deep px-1.5 py-0.5 text-xs text-shell-text border border-shell-border"
                            >
                              <option value="new">new</option>
                              <option value="seen">seen</option>
                              <option value="actioned">actioned</option>
                              <option value="discuss">discuss</option>
                            </select>
                            <button
                              type="button"
                              onClick={() => setRemoveConfirmEntry({ listId: entry.list_id, entryId: entry.id })}
                              aria-label={`Delete entry ${entry.text}`}
                              className="text-red-400 hover:underline"
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                        <p className={`text-sm ${entry.done ? "line-through text-shell-text-tertiary" : "text-shell-text"}`}>
                          {entry.text}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </main>
      </div>

      {createDialogOpen && (
        <CreateListDialog
          projectId={project.id}
          onClose={() => setCreateDialogOpen(false)}
          onCreated={handleCreatedList}
        />
      )}

      <ConfirmDialog
        open={!!deleteConfirmId}
        title="Delete list"
        message="This will delete the list and all its entries. This cannot be undone."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        danger
        onConfirm={confirmDeleteList}
        onCancel={() => setDeleteConfirmId(null)}
      />

      <ConfirmDialog
        open={!!removeConfirmEntry}
        title="Remove entry"
        message="Remove this entry from the list?"
        confirmLabel="Remove"
        cancelLabel="Cancel"
        danger
        onConfirm={confirmRemoveEntry}
        onCancel={() => setRemoveConfirmEntry(null)}
      />
    </div>
  );
}
