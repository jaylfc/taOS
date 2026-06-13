import { useCallback, useState } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Dialog from "@radix-ui/react-dialog";
import { CircleStop, OctagonX } from "lucide-react";
import { withCsrf } from "@/lib/csrf";

/* ------------------------------------------------------------------ */
/*  AgentKillSwitch                                                    */
/*  Top-bar quick access to stop a runaway agent without opening the  */
/*  Agents app: a dropdown with "Kill all" plus each running agent,    */
/*  every action gated behind a confirmation dialog (kill is          */
/*  destructive). Backend: POST /api/agents/bulk/stop and             */
/*  POST /api/agents/{name}/stop.                                      */
/* ------------------------------------------------------------------ */

interface RunningAgent {
  name: string;
  display_name?: string;
}

type Pending = { mode: "all" } | { mode: "one"; name: string; label: string } | null;

async function postStop(path: string): Promise<boolean> {
  try {
    const res = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers: withCsrf({ method: "POST" })?.headers,
    });
    return res.ok;
  } catch {
    return false;
  }
}

export function AgentKillSwitch() {
  const [agents, setAgents] = useState<RunningAgent[]>([]);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);

  // Refresh the running-agent list each time the menu opens (cheap, and keeps
  // the list current without a global store).
  const loadAgents = useCallback(async (open: boolean) => {
    if (!open) return;
    try {
      const res = await fetch("/api/agents", { credentials: "include" });
      if (!res.ok) return;
      const data = (await res.json()) as Array<Record<string, unknown>>;
      setAgents(
        (Array.isArray(data) ? data : [])
          .filter((a) => String(a.status ?? "") === "running")
          .map((a) => ({
            name: String(a.name ?? ""),
            display_name: a.display_name ? String(a.display_name) : undefined,
          }))
          .filter((a) => a.name),
      );
    } catch {
      // best-effort; leave the prior list
    }
  }, []);

  const confirmKill = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    const ok =
      pending.mode === "all"
        ? await postStop("/api/agents/bulk/stop")
        : await postStop(`/api/agents/${encodeURIComponent(pending.name)}/stop`);
    setBusy(false);
    if (ok) {
      window.dispatchEvent(new CustomEvent("taos:agents-changed"));
      setAgents((prev) =>
        pending.mode === "all" ? [] : prev.filter((a) => a.name !== pending.name),
      );
    }
    setPending(null);
  }, [pending]);

  const menuItem =
    "flex items-center gap-2.5 w-full px-3 py-2 text-sm rounded-md outline-none cursor-pointer select-none transition-colors";
  const dangerItem = `${menuItem} text-red-400 hover:bg-red-500/15 focus:bg-red-500/15`;
  const plainItem = `${menuItem} text-shell-text-secondary hover:bg-shell-surface-hover hover:text-shell-text focus:bg-shell-surface-hover focus:text-shell-text`;

  const dialogTitle = pending?.mode === "all" ? "Kill all agents?" : `Kill ${pending?.mode === "one" ? pending.label : ""}?`;
  const dialogBody =
    pending?.mode === "all"
      ? "Every running agent will be stopped immediately. In-flight work is lost. You can start them again from the Agents app."
      : "This agent will be stopped immediately. In-flight work is lost. You can start it again from the Agents app.";

  return (
    <>
      <DropdownMenu.Root onOpenChange={loadAgents}>
        <DropdownMenu.Trigger asChild>
          <button
            className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
            aria-label="Stop agents"
            title="Stop agents"
          >
            <CircleStop size={14} />
          </button>
        </DropdownMenu.Trigger>

        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={6}
            className="z-50 min-w-[200px] rounded-xl border border-shell-border p-1.5 shadow-2xl backdrop-blur-xl"
            style={{ backgroundColor: "rgba(28,26,44,0.96)" }}
          >
            <div className="px-3 pt-1.5 pb-1 text-[10px] uppercase tracking-wide text-shell-text-tertiary">
              Stop agents
            </div>

            <DropdownMenu.Item
              className={agents.length === 0 ? `${dangerItem} opacity-40 pointer-events-none` : dangerItem}
              onSelect={() => agents.length > 0 && setPending({ mode: "all" })}
            >
              <OctagonX size={14} />
              <span className="flex-1">Kill all agents</span>
              <span className="text-[10px] tabular-nums opacity-60">{agents.length}</span>
            </DropdownMenu.Item>

            {agents.length > 0 && (
              <DropdownMenu.Separator className="my-1 h-px bg-shell-border" />
            )}

            {agents.length === 0 ? (
              <div className="px-3 py-2 text-xs text-shell-text-tertiary">No running agents</div>
            ) : (
              agents.map((a) => {
                const label = a.display_name || a.name;
                return (
                  <DropdownMenu.Item
                    key={a.name}
                    className={plainItem}
                    onSelect={() => setPending({ mode: "one", name: a.name, label })}
                  >
                    <CircleStop size={14} className="text-shell-text-tertiary shrink-0" />
                    <span className="flex-1 truncate">{label}</span>
                  </DropdownMenu.Item>
                );
              })
            )}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      <Dialog.Root open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm" />
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-[61] w-[min(92vw,400px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-shell-border bg-shell-bg p-5 shadow-2xl"
            aria-describedby="kill-desc"
          >
            <div className="flex items-start gap-3">
              <span className="grid place-items-center h-9 w-9 rounded-xl bg-red-500/15 text-red-400 shrink-0">
                <OctagonX size={18} />
              </span>
              <div className="min-w-0">
                <Dialog.Title className="text-[15px] font-semibold text-shell-text">{dialogTitle}</Dialog.Title>
                <Dialog.Description id="kill-desc" className="mt-1 text-sm text-shell-text-secondary leading-relaxed">
                  {dialogBody}
                </Dialog.Description>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setPending(null)}
                disabled={busy}
                className="px-3.5 py-2 rounded-lg text-sm font-medium text-shell-text-secondary hover:bg-shell-surface-hover transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={confirmKill}
                disabled={busy}
                className="px-3.5 py-2 rounded-lg text-sm font-semibold bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {busy ? "Stopping..." : pending?.mode === "all" ? "Kill all" : "Kill agent"}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
