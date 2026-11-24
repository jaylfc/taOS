import { useCallback, useEffect, useRef, useState } from "react";
import { UserPlus } from "lucide-react";
import { getApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import {
  fetchPendingAgentInvites,
  revokePendingAgentInvite,
  PendingInvitesList,
  type PendingInvite,
} from "@/apps/ProjectsApp/PendingInvites";

/* ------------------------------------------------------------------ */
/*  PendingAgentInvites (#2067)                                        */
/*                                                                     */
/*  Pending external-agent invites in the registry area of the Agents  */
/*  app: agents in the process of joining should be visible on the     */
/*  agent-management surface, not only inside the mint dialog. Data    */
/*  comes from the read-only admin-gated aggregate                    */
/*  GET /api/agents/invites/pending; cancellation routes to the        */
/*  per-project or OS-level DELETE. Non-admins (403) see nothing —     */
/*  the aggregate spans projects they may not own.                     */
/* ------------------------------------------------------------------ */

export function PendingAgentInvites({
  active,
  refreshKey,
}: {
  active: boolean;
  refreshKey: number;
}) {
  const [invites, setInvites] = useState<PendingInvite[] | null>(null);
  const openWindow = useProcessStore((s) => s.openWindow);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    const my = ++seq.current;
    const rows = await fetchPendingAgentInvites();
    if (seq.current !== my) return; // stale; a newer refresh is in flight
    setInvites(rows);
  }, []);

  useEffect(() => {
    if (!active) return;
    void refresh();
    const t = setInterval(() => void refresh(), 30_000);
    return () => clearInterval(t);
  }, [active, refreshKey, refresh]);

  if (!active || invites === null) return null;

  const openProject = (invite: PendingInvite) => {
    if (!invite.project_id) return;
    const size = getApp("projects")?.defaultSize ?? { w: 1100, h: 720 };
    openWindow("projects", size, { projectId: invite.project_id, tab: "members" });
  };

  const cancel = async (invite: PendingInvite) => {
    await revokePendingAgentInvite(invite);
    void refresh();
  };

  return (
    <section className="mt-2" aria-label="Pending invites">
      <div className="flex items-center gap-2 text-xs text-shell-text-tertiary mb-2">
        <UserPlus size={12} aria-hidden />
        Pending invites ({invites.length})
      </div>
      <PendingInvitesList invites={invites} onRevoke={cancel} onOpenProject={openProject} />
    </section>
  );
}
