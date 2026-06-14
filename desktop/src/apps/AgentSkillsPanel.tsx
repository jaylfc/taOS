import { useState, useEffect, useCallback } from "react";
import { Wrench, Check, Loader2, Info } from "lucide-react";

interface Skill {
  id: string;
  name: string;
  category: string;
  description: string;
  frameworks: Record<string, string>;  // framework_id -> "native"|"adapter"|"unsupported"
  requires_services?: string[];
}

interface Props {
  agentId: string;
  framework: string;
}

const CATEGORY_COLORS: Record<string, string> = {
  search: "bg-blue-500/20 text-blue-400",
  files: "bg-amber-500/20 text-amber-400",
  code: "bg-slate-500/20 text-slate-400",
  media: "bg-pink-500/20 text-pink-400",
  browser: "bg-cyan-500/20 text-cyan-400",
  data: "bg-emerald-500/20 text-emerald-400",
  comms: "bg-cyan-500/20 text-cyan-400",
  system: "bg-slate-500/20 text-slate-400",
};

export function AgentSkillsPanel({ agentId, framework }: Props) {
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [assignedIds, setAssignedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const [allRes, assignedRes] = await Promise.all([
        fetch("/api/skills").then(r => r.json()),
        fetch(`/api/agents/${agentId}/skills`).then(r => r.json()),
      ]);
      setAllSkills(allRes.skills ?? []);
      setAssignedIds(new Set((assignedRes.skills ?? []).map((s: Skill) => s.id)));
    } catch {
      setAllSkills([]);
      setAssignedIds(new Set());
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = useCallback(async (skill: Skill, enable: boolean) => {
    setBusy(prev => new Set([...prev, skill.id]));
    try {
      if (enable) {
        await fetch(`/api/agents/${agentId}/skills`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ skill_id: skill.id }),
        });
        setAssignedIds(prev => new Set([...prev, skill.id]));
      } else {
        await fetch(`/api/agents/${agentId}/skills/${skill.id}`, { method: "DELETE" });
        setAssignedIds(prev => {
          const next = new Set(prev);
          next.delete(skill.id);
          return next;
        });
      }
    } catch { /* ignore */ }
    setBusy(prev => {
      const next = new Set(prev);
      next.delete(skill.id);
      return next;
    });
  }, [agentId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-shell-text-tertiary" />
      </div>
    );
  }

  // Partition skills
  const getLevel = (s: Skill) => s.frameworks[framework] ?? "unsupported";
  const enabled = allSkills.filter(s => assignedIds.has(s.id));
  const available = allSkills.filter(s => !assignedIds.has(s.id) && getLevel(s) !== "unsupported");
  const incompatible = allSkills.filter(s => getLevel(s) === "unsupported");

  const renderSkill = (skill: Skill, isEnabled: boolean, isGreyed = false) => {
    const level = getLevel(skill);
    const isBusy = busy.has(skill.id);
    return (
      <div
        key={skill.id}
        className={`flex items-start gap-3 p-3 rounded-xl border border-white/5 bg-white/[0.02] hover:bg-white/[0.04] transition-colors ${isGreyed ? "opacity-40" : ""}`}
      >
        <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0">
          <Wrench size={14} className="text-shell-text-secondary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-shell-text">{skill.name}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${CATEGORY_COLORS[skill.category] ?? "bg-white/10 text-white/60"}`}>
              {skill.category}
            </span>
            {!isGreyed && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-white/5 text-white/50">
                {level}
              </span>
            )}
          </div>
          <p className="text-xs text-shell-text-secondary">{skill.description}</p>
          {isGreyed && (
            <p className="text-[10px] text-shell-text-tertiary mt-1 italic">
              Not supported by {framework}
            </p>
          )}
          {skill.requires_services && skill.requires_services.length > 0 && (
            <div className="flex items-center gap-1 mt-1 text-[10px] text-shell-text-tertiary">
              <Info size={10} />
              <span>Requires: {skill.requires_services.join(", ")}</span>
            </div>
          )}
        </div>
        {!isGreyed && (
          <button
            onClick={() => toggle(skill, !isEnabled)}
            disabled={isBusy}
            className={`relative w-10 h-6 rounded-full transition-colors ${isEnabled ? "bg-accent" : "bg-white/10"} disabled:opacity-50`}
            aria-label={isEnabled ? "Disable skill" : "Enable skill"}
          >
            <div
              className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${isEnabled ? "translate-x-4" : "translate-x-0.5"}`}
            />
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-4 p-4 overflow-y-auto h-full">
      {enabled.length > 0 && (
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary mb-2 flex items-center gap-1.5">
            <Check size={12} className="text-emerald-400" />
            Enabled ({enabled.length})
          </h3>
          <div className="flex flex-col gap-2">
            {enabled.map(s => renderSkill(s, true))}
          </div>
        </section>
      )}

      {available.length > 0 && (
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary mb-2">
            Available ({available.length})
          </h3>
          <div className="flex flex-col gap-2">
            {available.map(s => renderSkill(s, false))}
          </div>
        </section>
      )}

      {incompatible.length > 0 && (
        <section>
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary mb-2">
            Incompatible ({incompatible.length})
          </h3>
          <div className="flex flex-col gap-2">
            {incompatible.map(s => renderSkill(s, false, true))}
          </div>
        </section>
      )}

      {allSkills.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-shell-text-tertiary">
          <Wrench size={32} className="mb-3" />
          <p className="text-sm">No skills available</p>
          <p className="text-xs mt-1">Skills will appear here once registered</p>
        </div>
      )}
    </div>
  );
}
