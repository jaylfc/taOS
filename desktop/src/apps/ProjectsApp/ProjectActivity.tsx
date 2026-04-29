import { useEffect, useState } from "react";
import { projectsApi, type ProjectActivity } from "@/lib/projects";

export function ProjectActivity({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ProjectActivity[]>([]);

  useEffect(() => {
    let cancelled = false;
    projectsApi
      .activity(projectId)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return (
    <ul className="space-y-1" aria-label="Activity">
      {items.map((a) => (
        <li
          key={a.id}
          className="bg-zinc-900 px-3 py-2 rounded text-sm flex flex-col gap-1 md:flex-row-reverse md:items-baseline md:justify-end md:gap-2"
        >
          <div className="md:contents">
            <span className="font-medium">{a.kind}</span>
            {a.payload && Object.keys(a.payload).length > 0 && (
              <span className="ml-2 text-zinc-500 text-xs">{JSON.stringify(a.payload)}</span>
            )}
          </div>
          <span className="text-zinc-500 text-xs md:text-sm md:tabular-nums">
            {new Date(a.created_at * 1000).toLocaleString()}
          </span>
        </li>
      ))}
      {items.length === 0 && <li className="text-sm text-zinc-500">No activity.</li>}
    </ul>
  );
}
