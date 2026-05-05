/**
 * BrowserApp v2 — SitePermissionsPanel.
 *
 * Table of all per-host permission grants for the current profile.
 * Columns: host_pattern | permission | state | revoke button
 *
 * Mounted as a section inside SettingsPanel, similar to AgentCapabilitiesPanel.
 */
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import {
  listSitePermissions,
  revokeSitePermission,
  type SitePermissionGrant,
} from "@/lib/browser-site-permissions-api";

interface SitePermissionsPanelProps {
  profileId: string;
  onClose(): void;
}

export function SitePermissionsPanel({ profileId, onClose }: SitePermissionsPanelProps) {
  const [grants, setGrants] = useState<SitePermissionGrant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revokingKey, setRevokingKey] = useState<string | null>(null);
  const loadSeqRef = useRef(0);

  const revokeKey = (g: SitePermissionGrant) => `${g.host_pattern}|${g.permission}`;

  async function load() {
    const seq = ++loadSeqRef.current;
    setError(null);
    try {
      const list = await listSitePermissions(profileId);
      if (seq !== loadSeqRef.current) return;
      setGrants(list);
    } catch {
      if (seq !== loadSeqRef.current) return;
      setError("Failed to load site permissions. Please try again.");
      setGrants([]);
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

  async function handleRevoke(grant: SitePermissionGrant) {
    const key = revokeKey(grant);
    if (revokingKey) return; // already revoking another (or same) row
    setRevokingKey(key);
    setError(null);
    try {
      const ok = await revokeSitePermission(profileId, grant.host_pattern, grant.permission);
      if (!ok) {
        setError("Failed to revoke permission. Please try again.");
        return;
      }
      await load();
    } catch {
      setError("Failed to revoke permission. Please try again.");
    } finally {
      setRevokingKey(null);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Site permissions"
        className="relative bg-shell-surface rounded-md shadow-xl border border-shell-border w-[560px] max-w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-shell-border-subtle">
          <h2 className="text-sm font-medium">Site permissions</h2>
          <button
            type="button"
            aria-label="Close site permissions"
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
            <p className="px-4 py-4 text-xs opacity-60 italic">No site permissions yet</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-shell-border-subtle text-shell-text-secondary">
                  <th className="px-4 py-2 text-left font-medium">Host pattern</th>
                  <th className="px-4 py-2 text-left font-medium">Permission</th>
                  <th className="px-4 py-2 text-left font-medium">State</th>
                  <th className="px-4 py-2 text-left font-medium sr-only">Revoke</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => (
                  <tr
                    key={`${grant.host_pattern}::${grant.permission}`}
                    className="border-b border-shell-border-subtle/40 hover:bg-shell-hover"
                  >
                    <td className="px-4 py-2">
                      <span
                        className="font-mono truncate max-w-[180px] block"
                        title={grant.host_pattern}
                      >
                        {grant.host_pattern}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <span className="px-1.5 py-0.5 rounded bg-shell-bg-deep text-shell-text-secondary text-[10px]">
                        {grant.permission}
                      </span>
                    </td>
                    <td className="px-4 py-2 capitalize">{grant.state}</td>
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        aria-label={`Revoke ${grant.permission} on ${grant.host_pattern}`}
                        onClick={() => handleRevoke(grant)}
                        disabled={revokingKey !== null}
                        className="p-1 rounded hover:bg-red-500/20 text-shell-text-secondary hover:text-red-400 disabled:opacity-50 disabled:cursor-not-allowed"
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
