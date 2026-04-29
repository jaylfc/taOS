import { useEffect, useState } from "react";
import { projectsApi, type Project, type ProjectMember } from "@/lib/projects";
import { AddAgentDialog } from "./AddAgentDialog";
import { canvasApi } from "./canvas/canvas-api";

export function ProjectMembers({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);

  const refresh = () =>
    projectsApi.members.list(project.id).then(setMembers).catch(() => setMembers([]));
  useEffect(() => {
    let cancelled = false;
    projectsApi.members
      .list(project.id)
      .then((rows) => {
        if (!cancelled) setMembers(rows);
      })
      .catch(() => {
        if (!cancelled) setMembers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  return (
    <section>
      <header className="flex justify-between mb-3">
        <h3 className="font-medium">Members</h3>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          className="text-sm px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700"
        >
          + Add agent
        </button>
      </header>
      <ul className="space-y-1" aria-label="Project members">
        {members.map((m) => (
          <li key={m.member_id} className="flex justify-between items-center bg-zinc-900 px-3 py-2 rounded">
            <div>
              <div className="text-sm">{m.member_id}</div>
              <div className="text-xs text-zinc-500">
                {m.member_kind}
                {m.member_kind === "clone" ? ` · ${m.memory_seed}` : ""}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {(m.member_kind === "native" || m.member_kind === "clone") && (
                <label
                  style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                  title="When off, this agent can add new elements but cannot modify or delete existing ones."
                >
                  <input
                    type="checkbox"
                    checked={!!m.can_edit_canvas}
                    onChange={async (e) => {
                      await canvasApi.setPermission(project.id, m.member_id, e.target.checked);
                      refresh();
                      onChanged();
                    }}
                  />
                  <span className="text-xs">Can edit canvas</span>
                </label>
              )}
              <button
                type="button"
                onClick={async () => {
                  await projectsApi.members.remove(project.id, m.member_id);
                  refresh();
                  onChanged();
                }}
                className="text-xs text-red-400 hover:underline"
                aria-label={`Remove ${m.member_id}`}
              >
                Remove
              </button>
            </div>
          </li>
        ))}
      </ul>
      {dialogOpen && (
        <AddAgentDialog
          projectId={project.id}
          onClose={() => setDialogOpen(false)}
          onAdded={() => {
            setDialogOpen(false);
            refresh();
            onChanged();
          }}
        />
      )}
    </section>
  );
}
