import { useCallback, useEffect, useRef, useState } from "react";
import { UserPlus } from "lucide-react";
import { getApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import {
  fetchPendingAgentInvites,
  fetchPendingAgentInvitesMine,
  revokePendingAgentInvite,
  PendingInvitesList,
  type PendingInvite,
} from "@/apps/ProjectsApp/PendingInvites";

/* ------------------------------------------------------------------ */
/*  PendingAgentInvites (#2067)                                        */
/*                                                                     */
/*  Pending external-agent invites in the registry area of the Agents  */
/*  app: agents in the process of joining should be visible on the     */
/*  agent-management surface, not only inside the mint dialog. Admins  */
/*  read the cross-project aggregate (GET /api/agents/invites/pending); */
/*  non-admin owners read their own agents' pending invites only       */
/*  (GET /api/agents/invites/pending/mine, not admin-gated).           */
/*  Cancellation routes to the per-project or OS-level DELETE.         */
/* ------------------------------------------------------------------ */

export function PendingAgentInvites({
  active,
  refreshKey,
  isAdmin,
}: {
  active: boolean;
  refreshKey: number;
  isAdmin: boolean;
}) {
  const [invites, setInvites] = useState<PendingInvite[] | null>(null);
  const openWindow = useProcessStore((s) => s.openWindow);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    const my = ++seq.current;
    const rows = isAdmin
      ? await fetchPendingAgentInvites()
      : await fetchPendingAgentInvitesMine();
    if (seq.current !== my) return; // stale; a newer refresh is in flight
    setInvites(rows);
  }, [isAdmin]);

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
