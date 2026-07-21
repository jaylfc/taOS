import { useEffect, useMemo, useState } from "react";
import { projectsApi, type Project, type ProjectMember } from "@/lib/projects";
import { AddAgentDialog } from "./AddAgentDialog";
import { InviteAgentDialog } from "./InviteAgentDialog";
import { canvasApi } from "./canvas/canvas-api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { withCsrf } from "@/lib/csrf";

interface AgentSummary {
  id: string;
  name: string;
  display_name?: string;
  emoji?: string;
  color?: string;
}

interface ExternalAgentSummary {
  handle: string;
  canonical_id?: string;
  display_name?: string;
  framework?: string;
}

// Friendly label for the agent-type pill (the framework an agent connects with).
function frameworkLabel(fw?: string): string {
  if (!fw) return "";
  const map: Record<string, string> = {
    "claude-code": "Claude Code",
    kilo: "Kilo",
    opencode: "opencode",
    hermes: "Hermes",
    "grok-build": "Grok",
    grok: "Grok",
  };
  return map[fw] || fw;
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
  framework,
  canonicalId,
  projectId,
  isLead,
  onRefresh,
  onRegistryRefresh,
  onChanged,
}: {
  member: ProjectMember;
  label: string;
  emoji?: string;
  hint?: string;
  isExternal?: boolean;
  framework?: string;
  canonicalId?: string;
  projectId: string;
  isLead?: boolean;
  onRefresh: () => void;
  onRegistryRefresh?: () => void;
  onChanged: () => void;
}) {
  const typeLabel = frameworkLabel(framework);
  const isAgent = member.member_kind === "native" || member.member_kind === "clone";
  const [confirmRemove, setConfirmRemove] = useState(false);
  // Registry-backed (external) agents can be renamed. display_name is a
  // registry-wide field, so the new name shows here and anywhere the agent
  // appears. Prefer the canonical id (member_id may be a legacy handle).
  const canRename = !!isExternal;
  const renameId = canonicalId || member.member_id;
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(label);
  const [renameError, setRenameError] = useState("");
  const [savingRename, setSavingRename] = useState(false);

  async function saveRename() {
    const next = nameDraft.trim();
    if (!next || next === label) {
      setRenaming(false);
      return;
    }
    setRenameError("");
    setSavingRename(true);
    try {
      const res = await fetch(
        `/api/agents/registry/${encodeURIComponent(renameId)}`,
        withCsrf({
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: next }),
        }),
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setRenameError(data?.error || `Rename failed (${res.status})`);
        return;
      }
      setRenaming(false);
      // Reload the registry roster so the new display_name is reflected in the
      // label (the members list alone does not carry display_name).
      onRegistryRefresh?.();
      onRefresh();
      onChanged();
    } catch {
      setRenameError("Network error, please try again");
    } finally {
      setSavingRename(false);
    }
  }
  return (
    <li
      className={
        isExternal
          ? "flex flex-col gap-2 border border-zinc-700/60 bg-zinc-900/60 px-3 py-3 rounded md:flex-row md:items-center md:justify-between md:gap-4 md:py-2"
          : "flex flex-col gap-2 bg-zinc-900 px-3 py-3 rounded md:flex-row md:items-center md:justify-between md:gap-4 md:py-2"
      }
    >
      <div className="min-w-0">
        {renaming ? (
          <div className="flex items-center gap-1 text-sm">
            <input
              type="text"
              value={nameDraft}
              autoFocus
              maxLength={64}
              aria-label={`New name for ${label}`}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !savingRename) saveRename();
                if (e.key === "Escape") {
                  setRenaming(false);
                  setNameDraft(label);
                  setRenameError("");
                }
              }}
              className="bg-zinc-800 rounded px-2 py-0.5 text-sm w-48"
            />
            <button
              type="button"
              onClick={saveRename}
              disabled={savingRename}
              className="text-xs px-2 py-0.5 bg-blue-600 rounded hover:bg-blue-500 text-white disabled:opacity-50"
            >
              {savingRename ? "Saving..." : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setRenaming(false);
                setNameDraft(label);
                setRenameError("");
              }}
              className="text-xs px-2 py-0.5 bg-zinc-800 rounded hover:bg-zinc-700"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="truncate text-sm flex items-center gap-1" title={hint || member.member_id}>
            {emoji && <span aria-hidden>{emoji}</span>}
            <span>{label}</span>
            {canRename && (
              <button
                type="button"
                onClick={() => {
                  setNameDraft(label);
                  setRenaming(true);
                }}
                className="text-[11px] text-zinc-500 hover:text-zinc-300"
                aria-label={`Rename ${label}`}
                title="Rename"
              >
                ✎
              </button>
            )}
            {isExternal && (
              <span className="ml-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/25">
                external
              </span>
            )}
            {typeLabel && (
              <span
                className="ml-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-zinc-700/40 text-zinc-300 border border-zinc-600/40"
                title="Agent type"
              >
                {typeLabel}
              </span>
            )}
            {isLead && (
              <span className="ml-1 text-xs text-yellow-400 font-medium" aria-label="Lead agent">
                ★ Lead
              </span>
            )}
          </div>
        )}
        {renameError && <div className="text-xs text-red-400">{renameError}</div>}
        {member.member_kind === "clone" && (
          <div className="text-xs text-zinc-500">clone · {member.memory_seed}</div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 md:flex-nowrap">
        {isAgent && (
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            title="When on, this agent can read (list, stream, snapshot) the canvas."
          >
            <input
              type="checkbox"
              checked={!!member.can_read_canvas}
              aria-label={`Can read canvas for ${label}`}
              onChange={async (e) => {
                await canvasApi.setPermission(projectId, member.member_id, "read", e.target.checked);
                onRefresh();
                onChanged();
              }}
            />
            <span className="text-xs">Can read canvas</span>
          </label>
        )}
        {isAgent && (
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            title="When off, this agent can add new elements but cannot modify or delete existing ones."
          >
            <input
              type="checkbox"
              checked={!!member.can_edit_canvas}
              aria-label={`Can edit canvas for ${label}`}
              onChange={async (e) => {
                await canvasApi.setPermission(projectId, member.member_id, "edit", e.target.checked);
                onRefresh();
                onChanged();
              }}
            />
            <span className="text-xs">Can edit canvas</span>
          </label>
        )}
        <button
          type="button"
          onClick={() => setConfirmRemove(true)}
          className="text-xs text-red-400 hover:underline"
          aria-label={`Remove ${label}`}
        >
          Remove
        </button>
      </div>
      <ConfirmDialog
        open={confirmRemove}
        title="Remove member"
        message={`Remove ${label} from this project? Their project access is revoked. This does not delete the agent's identity.`}
        confirmLabel="Remove"
        danger
        onCancel={() => setConfirmRemove(false)}
        onConfirm={async () => {
          setConfirmRemove(false);
          await projectsApi.members.remove(projectId, member.member_id);
          onRefresh();
          onChanged();
        }}
      />
    </li>
  );
}

export function ProjectMembers({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [externalAgents, setExternalAgents] = useState<ExternalAgentSummary[]>([]);
  const [externalRegistryLoaded, setExternalRegistryLoaded] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  // D7: the Lead is an exclusive, project-level designation. We keep a local
  // mirror of project.lead_member_id so the selector reflects changes instantly
  // (the backend enforces the one-lead invariant structurally on write).
  const [leadMemberId, setLeadMemberId] = useState<string | null>(project.lead_member_id ?? null);

  useEffect(() => {
    setLeadMemberId(project.lead_member_id ?? null);
  }, [project.lead_member_id]);

  const handleLeadChange = async (value: string) => {
    const next = value || null;
    setLeadMemberId(next);
    try {
      await projectsApi.setLead(project.id, next);
    } catch {
      // Revert on failure so the UI stays consistent with the server.
      setLeadMemberId(project.lead_member_id ?? null);
    }
    onChanged();
  };

  const labelFor = (m: ProjectMember): string => {
    if (byId.has(m.member_id)) return formatMemberLabel(m.member_id, byId).label;
    if (byHandle.has(m.member_id)) return formatExternalMemberLabel(m.member_id, byHandle).label;
    return m.member_id;
  };

  const refresh = () =>
    projectsApi.members.list(project.id).then(setMembers).catch(() => setMembers([]));

  // Reload the external registry roster so a rename (which updates a registry
  // display_name, not a project_members row) is reflected in the label. Members
  // list alone does not carry the display_name, so onRefresh is not enough.
  const reloadExternalRegistry = () =>
    fetch("/api/agents/registry", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => {
        if (!Array.isArray(rows)) return;
        const active = rows.filter(
          (e: { origin?: string; status?: string }) =>
            e.origin === "external-selfjoin" && e.status === "active",
        );
        setExternalAgents(
          active.map(
            (e: { handle?: string; canonical_id?: string; display_name?: string; framework?: string }) => ({
              handle: e.handle || "",
              canonical_id: e.canonical_id,
              display_name: e.display_name,
              framework: e.framework,
            }),
          ),
        );
      })
      .catch(() => {});

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
            active.map(
              (entry: {
                handle?: string;
                canonical_id?: string;
                display_name?: string;
                framework?: string;
              }) => ({
                handle: entry.handle || "",
                canonical_id: entry.canonical_id,
                display_name: entry.display_name,
                framework: entry.framework,
              }),
            ),
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
      // A project member references an external agent by its canonical id
      // (consent-flow agents), while older identities like @taOS-dev reference
      // it by handle. Key on both so an approved agent lands in the External
      // section either way.
      if (a.canonical_id) m.set(a.canonical_id, a);
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

  const leadOptions = useMemo(
    () => [...mainMembers, ...externalMembers],
    [mainMembers, externalMembers],
  );

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
        <button
          type="button"
          onClick={() => setInviteOpen(true)}
          className="text-sm px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700"
        >
          Invite external agent
        </button>
      </header>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label htmlFor="project-lead-select" className="text-xs text-zinc-400">
          Lead
        </label>
        <select
          id="project-lead-select"
          aria-label="Project lead"
          value={leadMemberId ?? ""}
          onChange={(e) => {
            void handleLeadChange(e.target.value);
          }}
          className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1"
        >
          <option value="">No lead</option>
          {leadOptions.map((m) => (
            <option key={m.member_id} value={m.member_id}>
              {labelFor(m)}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-zinc-500">
          exclusive per project
        </span>
      </div>
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
              isLead={m.member_id === leadMemberId}
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
                  isLead={m.member_id === leadMemberId}
                  isExternal
                  framework={byHandle.get(m.member_id)?.framework}
                  canonicalId={byHandle.get(m.member_id)?.canonical_id}
                  projectId={project.id}
                  onRefresh={refresh}
                  onRegistryRefresh={reloadExternalRegistry}
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
      {inviteOpen && (
        <InviteAgentDialog
          projectId={project.id}
          onClose={() => setInviteOpen(false)}
          onMinted={() => {
            // Do NOT close the dialog here. The mint succeeds and the dialog
            // then renders the invite URL and PIN, which are shown exactly
            // once and cannot be recovered afterwards. Closing on mint threw
            // that away and the user saw a dialog that vanished on success
            // (reported 2026-07-21). Refresh the member list only; the user
            // closes the dialog themselves once they have copied the details.
            onChanged();
          }}
        />
      )}
    </section>
  );
}
