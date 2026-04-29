import { useEffect, useState } from "react";
import { projectsApi, type ProjectTask } from "@/lib/projects";

type View = "ready" | "claimed" | "closed";

export function ProjectTaskList({ projectId }: { projectId: string }) {
  const [view, setView] = useState<View>("ready");
  const [tasks, setTasks] = useState<ProjectTask[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => {
        if (!cancelled && u?.id) setCurrentUserId(u.id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    try {
      if (view === "ready") setTasks(await projectsApi.tasks.ready(projectId));
      else if (view === "claimed") setTasks(await projectsApi.tasks.list(projectId, "claimed"));
      else setTasks(await projectsApi.tasks.list(projectId, "closed"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    refresh();
  }, [projectId, view]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const title = newTitle.trim();
    if (!title) return;
    try {
      await projectsApi.tasks.create(projectId, { title });
      setNewTitle("");
      setError(null);
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const claim = async (taskId: string) => {
    if (!currentUserId) return;
    try {
      await projectsApi.tasks.claim(projectId, taskId, currentUserId);
      setError(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const release = async (taskId: string) => {
    if (!currentUserId) return;
    try {
      await projectsApi.tasks.release(projectId, taskId, currentUserId);
      setError(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const close = async (taskId: string) => {
    if (!currentUserId) return;
    try {
      await projectsApi.tasks.close(projectId, taskId, currentUserId);
      setError(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  // Closed view filters older than 7 days client-side per spec default.
  const visible =
    view === "closed"
      ? tasks.filter((t) => (t.closed_at ?? 0) >= Date.now() / 1000 - 7 * 86400)
      : tasks;

  return (
    <section>
      <nav role="tablist" aria-label="Task view" className="flex gap-1 mb-3">
        {(["ready", "claimed", "closed"] as View[]).map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={view === v}
            onClick={() => setView(v)}
            className={`px-2 py-1 text-sm rounded ${
              view === v ? "bg-zinc-700" : "bg-zinc-900 text-zinc-400"
            }`}
          >
            {v}
          </button>
        ))}
      </nav>

      <form onSubmit={create} className="flex flex-col gap-2 mb-3 md:flex-row md:items-center md:gap-2">
        <input
          aria-label="New task title"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="Add a task…"
          className="w-full px-3 py-2 bg-zinc-800 rounded text-sm md:flex-1 md:px-2 md:py-1"
        />
        <button type="submit" className="px-4 py-2 bg-blue-600 rounded text-sm md:w-auto md:px-3 md:py-1">
          Add
        </button>
      </form>

      {error && <div role="alert" className="text-red-400 text-xs mb-2">{error}</div>}

      <ul className="space-y-1" aria-label={`${view} tasks`}>
        {visible.map((t) => {
          const ownsClaim = !!currentUserId && t.claimed_by === currentUserId;
          return (
            <li key={t.id} className="flex flex-col gap-2 bg-zinc-900 px-3 py-2 rounded md:flex-row md:items-center md:justify-between md:gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-sm truncate" title={t.title}>{t.title}</div>
                <div className="text-xs text-zinc-500 truncate">
                  {t.id}
                  {t.claimed_by ? ` · claimed by ${t.claimed_by}` : ""}
                  {t.closed_at ? ` · closed` : ""}
                </div>
              </div>
              <div className="flex flex-wrap gap-2 text-xs md:flex-nowrap">
                {view === "ready" && (
                  <button
                    type="button"
                    disabled={!currentUserId}
                    onClick={() => claim(t.id)}
                    className="px-2 py-1 bg-zinc-800 rounded disabled:opacity-50"
                  >
                    Claim
                  </button>
                )}
                {view === "claimed" && ownsClaim && (
                  <>
                    <button
                      type="button"
                      onClick={() => release(t.id)}
                      className="px-2 py-1 bg-zinc-800 rounded"
                    >
                      Release
                    </button>
                    <button
                      type="button"
                      onClick={() => close(t.id)}
                      className="px-2 py-1 bg-emerald-700 rounded"
                    >
                      Close
                    </button>
                  </>
                )}
              </div>
            </li>
          );
        })}
        {visible.length === 0 && <li className="text-sm text-zinc-500">No tasks.</li>}
      </ul>
    </section>
  );
}
