/**
 * Modal listing all agent capability grants for the current profile.
 * User can revoke individual grants. Mounted from SettingsPanel.
 *
 * Table columns: Agent | Host pattern | Permissions | Expires | Revoke
 */
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { listCapabilities, revokeCapability, type CapabilityGrant } from "@/lib/browser-capability-api";
import { listAgents, type AgentDto } from "@/lib/browser-agent-api";

export interface AgentCapabilitiesPanelProps {
  profileId: string;
  onClose(): void;
}

function formatExpiry(iso: string | null): string {
  if (!iso) return "Never";
  const parsed = new Date(iso).getTime();
  if (Number.isNaN(parsed)) return "Invalid";
  const ms = parsed - Date.now();
  if (ms < 0) return "Expired";
  const hours = Math.floor(ms / (60 * 60 * 1000));
  if (hours < 1) return "<1h";
  if (hours < 24) return `In ${hours}h`;
  const days = Math.floor(hours / 24);
  return `In ${days} day${days === 1 ? "" : "s"}`;
}

export function AgentCapabilitiesPanel({ profileId, onClose }: AgentCapabilitiesPanelProps) {
  const [grants, setGrants] = useState<CapabilityGrant[] | null>(null);
  const [agents, setAgents] = useState<AgentDto[]>([]);
  const [error, setError] = useState<string | null>(null);
  const loadSeqRef = useRef(0);

  async function load() {
    const seq = ++loadSeqRef.current;
    setError(null);
    try {
      const [fetchedGrants, fetchedAgents] = await Promise.all([
        listCapabilities(profileId),
        listAgents(),
      ]);
      if (seq !== loadSeqRef.current) return;
      setGrants(fetchedGrants);
      setAgents(fetchedAgents);
    } catch {
      if (seq !== loadSeqRef.current) return;
      setError("Failed to load capabilities. Please try again.");
      setGrants([]);
      setAgents([]);
    }
  }

  useEffect(() => {
    load();
  }, [profileId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  async function handleRevoke(grant: CapabilityGrant) {
    setError(null);
    try {
      const ok = await revokeCapability(profileId, grant.agent_id, grant.host_pattern);
      if (!ok) {
        setError("Failed to revoke capability. Please try again.");
        return;
      }
      // Refresh list
      const fresh = await listCapabilities(profileId);
      setGrants(fresh);
    } catch {
      setError("Failed to revoke capability. Please try again.");
    }
  }

  function agentName(agentId: string): string {
    const found = agents.find((a) => a.id === agentId);
    return found ? found.name : agentId;
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Agent capabilities"
        className="relative bg-shell-surface rounded-md shadow-xl border border-shell-border w-[600px] max-w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-shell-border-subtle">
          <h2 className="text-sm font-medium">Agent capabilities</h2>
          <button
            type="button"
            aria-label="Close agent capabilities"
            onClick={onClose}
            className="p-1 rounded hover:bg-shell-hover"
          >
            <X size={16} />
          </button>
        </header>

        {error && (
          <div className="mx-4 mt-3 px-3 py-2 rounded bg-red-500/10 border border-red-500/30 text-red-400 text-xs">
            {error}
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {grants === null ? (
            <p className="px-4 py-4 text-xs opacity-60 italic">Loading…</p>
          ) : grants.length === 0 ? (
            <p className="px-4 py-4 text-xs opacity-60 italic">No agent capabilities granted yet</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-shell-border-subtle text-shell-text-secondary">
                  <th className="px-4 py-2 text-left font-medium">Agent</th>
                  <th className="px-4 py-2 text-left font-medium">Host pattern</th>
                  <th className="px-4 py-2 text-left font-medium">Permissions</th>
                  <th className="px-4 py-2 text-left font-medium">Expires</th>
                  <th className="px-4 py-2 text-left font-medium sr-only">Revoke</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => (
                  <tr
                    key={`${grant.agent_id}::${grant.host_pattern}`}
                    className="border-b border-shell-border-subtle/40 hover:bg-shell-hover"
                  >
                    <td className="px-4 py-2">{agentName(grant.agent_id)}</td>
                    <td className="px-4 py-2">
                      <span
                        className="font-mono truncate max-w-[160px] block"
                        title={grant.host_pattern}
                      >
                        {grant.host_pattern}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {grant.permissions.split(",").map((p) => (
                          <span
                            key={p}
                            className="px-1.5 py-0.5 rounded bg-shell-bg-deep text-shell-text-secondary text-[10px]"
                          >
                            {p.trim()}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      {formatExpiry(grant.expires_at)}
                    </td>
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        aria-label={`Revoke ${grant.permissions} on ${grant.host_pattern}`}
                        onClick={() => handleRevoke(grant)}
                        className="p-1 rounded hover:bg-red-500/20 text-shell-text-secondary hover:text-red-400"
                      >
                        <X size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
