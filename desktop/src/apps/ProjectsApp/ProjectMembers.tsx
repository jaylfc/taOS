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

interface ExternalAgentSummary {
  handle: string;
  display_name?: string;
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

function formatExternalMemberLabel(
  memberId: string,
  byHandle: Map<string, ExternalAgentSummary>,
): {
  label: string;
  hint?: string;
} {
  const entry = byHandle.get(memberId);
  if (entry) {
    const label = entry.display_name || entry.handle || memberId;
    const hint =
      entry.display_name && entry.handle && entry.display_name !== entry.handle
        ? entry.handle
        : undefined;
    return { label, hint };
  }
  return { label: memberId };
}

function MemberRow({
  member,
  label,
  emoji,
  hint,
  isExternal,
  projectId,
  onRefresh,
  onChanged,
}: {
  member: ProjectMember;
  label: string;
  emoji?: string;
  hint?: string;
  isExternal?: boolean;
  projectId: string;
  onRefresh: () => void;
  onChanged: () => void;
}) {
  return (
    <li
      className={
        isExternal
          ? "flex flex-col gap-2 border border-zinc-700/60 bg-zinc-900/60 px-3 py-3 rounded md:flex-row md:items-center md:justify-between md:gap-4 md:py-2"
          : "flex flex-col gap-2 bg-zinc-900 px-3 py-3 rounded md:flex-row md:items-center md:justify-between md:gap-4 md:py-2"
      }
    >
      <div className="min-w-0">
        <div className="truncate text-sm flex items-center gap-1" title={hint || member.member_id}>
          {emoji && <span aria-hidden>{emoji}</span>}
          <span>{label}</span>
          {isExternal && (
            <span className="ml-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/25">
              external
            </span>
          )}
          {!!member.is_lead && (
            <span className="ml-1 text-xs text-yellow-400 font-medium" aria-label="Lead agent">
              ★ Lead
            </span>
          )}
        </div>
        <div className="text-xs text-zinc-500">
          {member.member_kind}
          {member.member_kind === "clone" ? ` · ${member.memory_seed}` : ""}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 md:flex-nowrap">
        {!isExternal && (member.member_kind === "native" || member.member_kind === "clone") && (
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            title="When off, this agent can add new elements but cannot modify or delete existing ones."
          >
            <input
              type="checkbox"
              checked={!!member.can_edit_canvas}
              onChange={async (e) => {
                await canvasApi.setPermission(projectId, member.member_id, e.target.checked);
                onRefresh();
                onChanged();
              }}
            />
            <span className="text-xs">Can edit canvas</span>
          </label>
        )}
        {!isExternal && (member.member_kind === "native" || member.member_kind === "clone") && (
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            title="Lead agents see all messages in the project channel, even without being @mentioned."
          >
            <input
              type="checkbox"
              checked={!!member.is_lead}
              aria-label={`Toggle lead for ${label}`}
              onChange={async (e) => {
                await projectsApi.members.setLead(projectId, member.member_id, e.target.checked);
                onRefresh();
                onChanged();
              }}
            />
            <span className="text-xs">Lead</span>
          </label>
        )}
        <button
          type="button"
          onClick={async () => {
            await projectsApi.members.remove(projectId, member.member_id);
            onRefresh();
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
}

export function ProjectMembers({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [externalAgents, setExternalAgents] = useState<ExternalAgentSummary[]>([]);
  const [externalRegistryLoaded, setExternalRegistryLoaded] = useState(false);
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

  useEffect(() => {
    let cancelled = false;
    fetch("/api/agents/registry", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => {
        if (cancelled) return;
        if (Array.isArray(rows)) {
          const active = rows.filter(
            (entry: { origin?: string; status?: string }) =>
              entry.origin === "external-selfjoin" && entry.status === "active",
          );
          setExternalAgents(
            active.map((entry: { handle?: string; display_name?: string }) => ({
              handle: entry.handle || "",
              display_name: entry.display_name,
            })),
          );
        }
        setExternalRegistryLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setExternalRegistryLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const byId = useMemo(() => {
    const m = new Map<string, AgentSummary>();
    for (const a of agents) m.set(a.id, a);
    return m;
  }, [agents]);

  const byHandle = useMemo(() => {
    const m = new Map<string, ExternalAgentSummary>();
    for (const a of externalAgents) {
      if (a.handle) m.set(a.handle, a);
    }
    return m;
  }, [externalAgents]);

  const { mainMembers, externalMembers } = useMemo(() => {
    const main: ProjectMember[] = [];
    const external: ProjectMember[] = [];
    for (const m of members) {
      if (byId.has(m.member_id)) {
        main.push(m);
      } else if (byHandle.has(m.member_id)) {
        external.push(m);
      } else if (externalRegistryLoaded) {
        main.push(m);
      }
    }
    return { mainMembers: main, externalMembers: external };
  }, [members, byId, byHandle, externalRegistryLoaded]);

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
        {mainMembers.map((m) => {
          const { label, emoji, hint } = formatMemberLabel(m.member_id, byId);
          return (
            <MemberRow
              key={m.member_id}
              member={m}
              label={label}
              emoji={emoji}
              hint={hint}
              projectId={project.id}
              onRefresh={refresh}
              onChanged={onChanged}
            />
          );
        })}
      </ul>
      {externalMembers.length > 0 && (
        <section className="mt-5 pt-4 border-t border-zinc-800">
          <h4 className="text-sm font-medium text-zinc-300 mb-2">External / Connected agents</h4>
          <ul className="space-y-1" aria-label="External project members">
            {externalMembers.map((m) => {
              const { label, hint } = formatExternalMemberLabel(m.member_id, byHandle);
              return (
                <MemberRow
                  key={m.member_id}
                  member={m}
                  label={label}
                  hint={hint}
                  isExternal
                  projectId={project.id}
                  onRefresh={refresh}
                  onChanged={onChanged}
                />
              );
            })}
          </ul>
        </section>
      )}
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