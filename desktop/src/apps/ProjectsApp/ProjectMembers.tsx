import { useEffect, useMemo, useState } from "react";
import { projectsApi, type Project, type ProjectMember } from "@/lib/projects";
import { AddAgentDialog } from "./AddAgentDialog";
import { canvasApi } from "./canvas/canvas-api";

interface AgentSummary {
  id: string;
  name: string;
  display_name?: string;
  emoji?: string;
  color?: string;
}

function formatMemberLabel(memberId: string, byId: Map<string, AgentSummary>): {
  label: string;
  emoji?: string;
  hint?: string;
} {
  const agent = byId.get(memberId);
  if (agent) {
    return {
      label: agent.display_name || agent.name,
      emoji: agent.emoji,
      hint: agent.name !== (agent.display_name || agent.name) ? agent.name : undefined,
    };
  }
  return { label: memberId };
}

export function ProjectMembers({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
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

  // Fetch the agent roster once per mount so member rows can render names + emoji
  // instead of opaque hex IDs. Falls back gracefully if the call fails.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => {
        if (!cancelled && Array.isArray(rows)) setAgents(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const byId = useMemo(() => {
    const m = new Map<string, AgentSummary>();
    for (const a of agents) m.set(a.id, a);
    return m;
  }, [agents]);

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
        {members.map((m) => {
          const { label, emoji, hint } = formatMemberLabel(m.member_id, byId);
          return (
            <li key={m.member_id} className="flex flex-col gap-2 bg-zinc-900 px-3 py-3 rounded md:flex-row md:items-center md:justify-between md:gap-4 md:py-2">
              <div className="min-w-0">
                <div className="truncate text-sm flex items-center gap-1" title={hint || m.member_id}>
                  {emoji && <span aria-hidden>{emoji}</span>}
                  <span>{label}</span>
                  {!!m.is_lead && (
                    <span
                      className="ml-1 text-xs text-yellow-400 font-medium"
                      aria-label="Lead agent"
                    >
                      ★ Lead
                    </span>
                  )}
                </div>
                <div className="text-xs text-zinc-500">
                  {m.member_kind}
                  {m.member_kind === "clone" ? ` · ${m.memory_seed}` : ""}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:flex-nowrap">
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
                {(m.member_kind === "native" || m.member_kind === "clone") && (
                  <label
                    style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                    title="Lead agents see all messages in the project channel, even without being @mentioned."
                  >
                    <input
                      type="checkbox"
                      checked={!!m.is_lead}
                      aria-label={`Toggle lead for ${label}`}
                      onChange={async (e) => {
                        await projectsApi.members.setLead(project.id, m.member_id, e.target.checked);
                        refresh();
                        onChanged();
                      }}
                    />
                    <span className="text-xs">Lead</span>
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
                  aria-label={`Remove ${label}`}
                >
                  Remove
                </button>
              </div>
            </li>
          );
        })}
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
