import { useState, useEffect, useCallback, useRef } from "react";
import { Radar, Pause, Play, Loader2, CircleDot, Minus, Plus, AlertCircle } from "lucide-react";
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

function coerceLaneCaps(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === "object") {
    for (const [k, raw] of Object.entries(v as Record<string, unknown>)) {
      const c = coerceCap(raw);
      if (c != null) out[k] = c;
    }
  }
  return out;
}

// Shared concurrency-cap stepper for the global steer and each lane. The lower
// button floors at 1 and disables there (removal is the explicit Clear the
// caller renders), and the raise button stops at MAX_CAP.
function CapStepper({
  value,
  busy,
  label,
  onChange,
}: {
  value: number | null;
  busy: boolean;
  label: string;
  onChange: (next: number | null) => void;
}) {
  const btn =
    "flex h-7 w-7 items-center justify-center rounded-md border border-shell-border text-shell-text-secondary transition-colors hover:text-shell-text hover:border-shell-border-strong disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => value != null && value > 1 && onChange(value - 1)}
        disabled={busy || value == null || value <= 1}
        aria-label={`Lower ${label}`}
        className={btn}
      >
        <Minus size={14} />
      </button>
      <span
        className="min-w-[3.5rem] text-center text-sm font-medium text-shell-text tabular-nums"
        aria-label={`${label} value`}
      >
        {value == null ? "No cap" : value}
      </span>
      <button
        type="button"
        onClick={() => onChange(Math.min(MAX_CAP, (value ?? 0) + 1))}
        disabled={busy || (value != null && value >= MAX_CAP)}
        aria-label={`Raise ${label}`}
        className={btn}
      >
        <Plus size={14} />
      </button>
    </div>
  );
}

export function ObservatoryApp({ windowId: _windowId }: { windowId: string }) {
  const [agents, setAgents] = useState<FleetAgent[]>([]);
  const [pause, setPause] = useState<PauseState>(EMPTY_PAUSE);
  const [cap, setCap] = useState<number | null>(null);
  const [laneCaps, setLaneCaps] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [steerError, setSteerError] = useState<string | null>(null);
  // Count of steer writes in flight. The fleet list always refreshes, but the
  // server's steer-state (pause/caps) is adopted only when this is 0, so an
  // optimistic value is never reverted by a concurrent read -- neither the 5s
  // background poll nor another write's reconcile. The reconcile after the last
  // write (inFlight back to 0) is the one that adopts.
  const inFlight = useRef(0);

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    try {
      const [fleetRes, throttleRes] = await Promise.all([
        fetch("/api/observatory/fleet"),
        fetch("/api/observatory/throttle"),
      ]);
      const fleetData = fleetRes.ok ? await fleetRes.json() : null;
      const throttleData = throttleRes.ok ? await throttleRes.json() : null;
      if (fleetData) {
        setAgents(Array.isArray(fleetData.agents) ? fleetData.agents : []);
      }
      if (inFlight.current === 0) {
        if (fleetData) {
          setPause(
            fleetData.paused && typeof fleetData.paused === "object"
              ? fleetData.paused
              : EMPTY_PAUSE,
          );
        }
        if (throttleData) {
          setCap(coerceCap(throttleData?.global));
          setLaneCaps(coerceLaneCaps(throttleData?.lanes));
        }
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
    const id = setInterval(() => {
      // Skip the poll while a steer write is in flight so it cannot revert an
      // optimistic value mid-request; postSteer reconciles after the write.
      if (inFlight.current === 0) load({ silent: true });
    }, 5000);
    return () => clearInterval(id);
  }, [load]);

  // Shared write path for every steer control. Posts the change, surfaces a
  // visible error if the server rejects it (so an optimistic value is not left
  // standing silently), and always reconciles against the server. A sequence
  // guard ensures only the latest write controls the banner: steer controls are
  // not fully serialized (pause and a cap change use different busy scopes), so
  // an earlier slow response must not overwrite a newer one's result.
  const steerSeq = useRef(0);
  const postSteer = useCallback(
    async (url: string, body: object, failMsg: string) => {
      inFlight.current += 1;
      const seq = ++steerSeq.current;
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (seq === steerSeq.current) setSteerError(res.ok ? null : failMsg);
      } catch {
        if (seq === steerSeq.current) setSteerError(failMsg);
      } finally {
        // Decrement first so this reconcile adopts the server steer-state only
        // when it is the last write to finish; an earlier concurrent write's
        // reconcile (inFlight still > 0) refreshes the fleet but not the caps.
        inFlight.current -= 1;
        await load({ silent: true });
      }
    },
    [load],
  );

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
      await postSteer(
        "/api/observatory/pause",
        { scope, paused },
        "Could not update the pause state.",
      );
      setBusy(null);
    },
    [postSteer],
  );

  const setGlobalCap = useCallback(
    async (next: number | null) => {
      setBusy("cap");
      setCap(next); // optimistic; reconciled on the next poll
      await postSteer(
        "/api/observatory/throttle",
        { scope: "global", max_concurrent: next },
        "Could not update the concurrency cap.",
      );
      setBusy(null);
    },
    [postSteer],
  );

  const setLaneCap = useCallback(
    async (handle: string, next: number | null) => {
      setBusy(`cap:${handle}`);
      setLaneCaps((prev) => {
        const copy = { ...prev };
        if (next == null) delete copy[handle];
        else copy[handle] = next;
        return copy;
      });
      await postSteer(
        "/api/observatory/throttle",
        { scope: handle, max_concurrent: next },
        `Could not update the cap for ${handle}.`,
      );
      setBusy(null);
    },
    [postSteer],
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

      {steerError && (
        <div
          className="flex items-center gap-2 border-b border-red-500/20 bg-red-500/10 px-5 py-2 text-sm text-red-400"
          role="alert"
        >
          <AlertCircle size={14} className="shrink-0" />
          {steerError}
          <button
            type="button"
            onClick={() => setSteerError(null)}
            className="ml-auto text-xs text-red-400/80 transition-colors hover:text-red-400"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Steer: global concurrency cap (volume knob alongside the pause switch) */}
      <div className="flex items-center gap-3 border-b border-shell-border px-5 py-2.5">
        <span className="text-xs font-medium uppercase tracking-wide text-shell-text-tertiary">
          Concurrency cap
        </span>
        <CapStepper
          value={cap}
          busy={busy === "cap"}
          label="concurrency cap"
          onChange={setGlobalCap}
        />
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
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-x-3 gap-y-1.5">
                    <label className="flex items-center gap-1.5 text-xs text-shell-text-tertiary">
                      Pause
                      <Switch
                        checked={lanePaused}
                        disabled={busy === a.handle}
                        onCheckedChange={(v: boolean) => setScope(a.handle, v)}
                        aria-label={`Pause lane ${a.handle}`}
                      />
                    </label>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs text-shell-text-tertiary">Limit</span>
                      <CapStepper
                        value={laneCaps[a.handle] ?? null}
                        busy={busy === `cap:${a.handle}`}
                        label={`${a.handle} limit`}
                        onChange={(n) => setLaneCap(a.handle, n)}
                      />
                      {laneCaps[a.handle] != null && (
                        <button
                          type="button"
                          onClick={() => setLaneCap(a.handle, null)}
                          disabled={busy === `cap:${a.handle}`}
                          className="text-xs text-shell-text-tertiary transition-colors hover:text-shell-text"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
