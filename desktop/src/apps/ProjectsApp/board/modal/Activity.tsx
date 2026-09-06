import { useEffect, useState } from "react";
import { projectsApi } from "../../../../lib/projects";
import type { ProjectComment } from "../../../../lib/projects";

export interface ActivityProps {
  projectId: string;
  taskId: string;
  currentUserId: string;
}

export function Activity({ projectId, taskId, currentUserId }: ActivityProps) {
  const [comments, setComments] = useState<ProjectComment[]>([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const items = await projectsApi.tasks.listComments(projectId, taskId);
      if (!cancelled) setComments(items);
    })();
    return () => { cancelled = true; };
  }, [projectId, taskId]);

  const submit = async () => {
    const body = draft.trim();
    if (!body || !currentUserId) return;
    const c = await projectsApi.tasks.addComment(projectId, taskId, { body, author_id: currentUserId });
    setComments(prev => [...prev, c]);
    setDraft("");
  };

  return (
    <section className="board-section">
      <h3>Activity</h3>
      <ul>
        {comments.map(c => (
          <li key={c.id}><b>{c.author_id}</b>: {c.body}</li>
        ))}
      </ul>
      <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Comment…"
          aria-label="Comment composer"
        />
        <button type="submit" disabled={!currentUserId || !draft.trim()}>↵ Send</button>
      </form>
    </section>
  );
}
