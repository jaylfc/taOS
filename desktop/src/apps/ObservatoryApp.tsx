import { useState, useEffect, useCallback } from "react";
import { Radar, Pause, Play, Loader2, CircleDot, Minus, Plus } from "lucide-react";
import { Switch } from "@/components/ui";

interface HeldCard {
  task_id: string;
  project_id: string;
  title: string | null;
}

interface FleetAgent {
  handle: string;
  state: string;
  holds: HeldCard | null;
}

interface PauseState {
  global: boolean;
  lanes: Record<string, boolean>;
}

const EMPTY_PAUSE: PauseState = { global: false, lanes: {} };

// Global concurrency cap: how many cards the fleet may hold in flight at once
// (the dispatch loop reads it as MAX_OPEN_PRS). null = no override, the loop
// default applies. Pause is the on/off switch; this is the volume knob.
const MAX_CAP = 50; // sane ceiling so the stepper cannot post runaway values
function coerceCap(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
  return Math.min(MAX_CAP, Math.floor(v));
}

export function ObservatoryApp({ windowId: _windowId }: { windowId: string }) {
  const [agents, setAgents] = useState<FleetAgent[]>([]);
  const [pause, setPause] = useState<PauseState>(EMPTY_PAUSE);
  const [cap, setCap] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    try {
      const [fleetRes, throttleRes] = await Promise.all([
        fetch("/api/observatory/fleet"),
        fetch("/api/observatory/throttle"),
      ]);
      if (fleetRes.ok) {
        const data = await fleetRes.json();
        setAgents(Array.isArray(data.agents) ? data.agents : []);
        setPause(
          data.paused && typeof data.paused === "object" ? data.paused : EMPTY_PAUSE,
        );
      }
      if (throttleRes.ok) {
        const data = await throttleRes.json();
        setCap(coerceCap(data?.global));
      }
    } catch {
      // Non-critical: keep the last-loaded view.
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Poll so the fleet + pause state stay live without a manual refresh.
    const id = setInterval(() => load({ silent: true }), 5000);
    return () => clearInterval(id);
  }, [load]);

  const setScope = useCallback(
    async (scope: string, paused: boolean) => {
      setBusy(scope);
      // Optimistic: reflect the toggle immediately, reconcile on refresh.
      setPause((prev) =>
        scope === "global"
          ? { ...prev, global: paused }
          : {
              ...prev,
              lanes: { ...prev.lanes, [scope]: paused },
            },
      );
      try {
        await fetch("/api/observatory/pause", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope, paused }),
        });
        await load({ silent: true });
      } catch {
        await load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  const setGlobalCap = useCallback(
    async (next: number | null) => {
      setBusy("cap");
      setCap(next); // optimistic; reconciled on the next poll
      try {
        await fetch("/api/observatory/throttle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope: "global", max_concurrent: next }),
        });
        await load({ silent: true });
      } catch {
        await load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  return (
    <div className="flex h-full flex-col overflow-hidden bg-shell-bg">
      {/* Header + global steer */}
      <div className="flex items-center gap-2 border-b border-shell-border px-5 py-4">
        <Radar size={18} className="text-accent" />
        <h1 className="text-base font-semibold text-shell-text">Observatory</h1>
        <button
          type="button"
          onClick={() => setScope("global", !pause.global)}
          disabled={busy === "global"}
          aria-pressed={pause.global}
          className={[
            "ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
            pause.global
              ? "bg-amber-500/15 text-amber-400 hover:bg-amber-500/25"
              : "border border-shell-border text-shell-text-secondary hover:text-shell-text hover:border-shell-border-strong",
          ].join(" ")}
        >
          {pause.global ? <Play size={15} /> : <Pause size={15} />}
          {pause.global ? "Resume queue" : "Pause queue"}
        </button>
      </div>

      {pause.global && (
        <div
          className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/10 px-5 py-2 text-sm text-amber-400"
          role="status"
        >
          <Pause size={14} className="shrink-0" />
          Dispatch is paused. In-flight work finishes; no new cards are claimed.
        </div>
      )}

      {/* Steer: global concurrency cap (volume knob alongside the pause switch) */}
      <div className="flex items-center gap-3 border-b border-shell-border px-5 py-2.5">
        <span className="text-xs font-medium uppercase tracking-wide text-shell-text-tertiary">
          Concurrency cap
        </span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => cap != null && cap > 1 && setGlobalCap(cap - 1)}
            disabled={busy === "cap" || cap == null || cap <= 1}
            aria-label="Lower concurrency cap"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-shell-border text-shell-text-secondary transition-colors hover:text-shell-text hover:border-shell-border-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Minus size={14} />
          </button>
          <span
            className="min-w-[3.5rem] text-center text-sm font-medium text-shell-text tabular-nums"
            aria-label="Concurrency cap value"
          >
            {cap == null ? "No cap" : cap}
          </span>
          <button
            type="button"
            onClick={() => setGlobalCap(Math.min(MAX_CAP, (cap ?? 0) + 1))}
            disabled={busy === "cap" || (cap != null && cap >= MAX_CAP)}
            aria-label="Raise concurrency cap"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-shell-border text-shell-text-secondary transition-colors hover:text-shell-text hover:border-shell-border-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus size={14} />
          </button>
        </div>
        {cap != null && (
          <button
            type="button"
            onClick={() => setGlobalCap(null)}
            disabled={busy === "cap"}
            className="text-xs text-shell-text-tertiary transition-colors hover:text-shell-text"
          >
            Clear
          </button>
        )}
        <span className="ml-auto text-xs text-shell-text-tertiary">
          Max cards the fleet holds at once
        </span>
      </div>

      {/* Fleet (Observe) */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-shell-text-tertiary">
          Fleet
        </h2>
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-shell-text-tertiary">
            <Loader2 size={14} className="animate-spin" />
            Loading...
          </p>
        ) : agents.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center">
            <Radar size={28} className="text-shell-text-tertiary" />
            <p className="text-sm text-shell-text-secondary">All lanes idle.</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {agents.map((a) => {
              const lanePaused = !!pause.lanes[a.handle];
              return (
                <li
                  key={a.handle + (a.holds?.task_id ?? "")}
                  className="flex items-center gap-3 rounded-xl border border-shell-border bg-shell-surface px-4 py-3"
                >
                  <CircleDot
                    size={14}
                    className={lanePaused ? "shrink-0 text-amber-400" : "shrink-0 text-green-400"}
                  />
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-sm font-medium text-shell-text">
                      {a.handle}
                    </span>
                    {a.holds ? (
                      <span className="truncate text-xs text-shell-text-secondary">
                        {lanePaused ? "paused" : "working"} &middot; {a.holds.title ?? a.holds.task_id}
                      </span>
                    ) : (
                      <span className="text-xs text-shell-text-tertiary">
                        {lanePaused ? "paused" : a.state}
                      </span>
                    )}
                  </div>
                  <label className="flex shrink-0 items-center gap-1.5 text-xs text-shell-text-tertiary">
                    Pause
                    <Switch
                      checked={lanePaused}
                      disabled={busy === a.handle}
                      onCheckedChange={(v: boolean) => setScope(a.handle, v)}
                      aria-label={`Pause lane ${a.handle}`}
                    />
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
