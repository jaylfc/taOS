import { useState } from "react";
import { Zap, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui";

// #1743: a recovery action in the Activity tab. On edge devices a local model
// can stall (endless NPU generation, unresponsive inference) while the
// controller stays up, and recovery previously meant SSH + `systemctl restart
// rkllama qmd`. This restarts those backend services from the UI, with a
// confirmation first since it interrupts any running inference.

interface UnitResult {
  unit: string;
  ok?: boolean;
  scope?: string;
  detail?: string;
}

interface RestartResponse {
  status: string;
  restarted: string[];
  failed: UnitResult[];
  results: UnitResult[];
}

type Phase = "idle" | "confirm" | "running" | "done" | "error";

const unitLabel = (unit: string) => unit.replace(/\.service$/, "");

export function AiStackRecovery({ onRecovered }: { onRecovered?: () => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<RestartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setPhase("running");
    setError(null);
    try {
      const r = await fetch("/api/system/ai-stack/restart", { method: "POST" });
      const body = await r.json().catch(() => null);
      if (r.status === 403) {
        setError("Admin access is required to restart AI services.");
        setPhase("error");
        return;
      }
      if (!r.ok || !body) {
        setError((body && body.error) || "The restart request failed.");
        setPhase("error");
        return;
      }
      setResult(body as RestartResponse);
      setPhase("done");
      onRecovered?.();
    } catch (e) {
      setError((e as Error).message || "Network error");
      setPhase("error");
    }
  };

  const close = () => {
    setPhase("idle");
    setResult(null);
    setError(null);
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setPhase("confirm")}
        aria-label="Restart AI services"
        className="gap-1.5 text-shell-text-secondary hover:text-shell-text"
      >
        <Zap size={14} aria-hidden="true" />
        Restart AI Services
      </Button>

      {phase !== "idle" && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Restart AI services"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
        >
          <div className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-md shadow-xl space-y-4">
            {phase === "confirm" && (
              <>
                <h3 className="text-base font-semibold flex items-center gap-2">
                  <AlertTriangle size={16} className="text-amber-400" aria-hidden="true" />
                  Restart AI services?
                </h3>
                <p className="text-sm text-shell-text-secondary">
                  This restarts the local inference backends (rkllama and qmd) and interrupts any
                  running generation. The desktop and your agents stay up.
                </p>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={close}>
                    Cancel
                  </Button>
                  <Button variant="default" size="sm" onClick={run}>
                    Restart
                  </Button>
                </div>
              </>
            )}

            {phase === "running" && (
              <p className="text-sm flex items-center gap-2 text-shell-text-secondary">
                <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                Restarting AI services…
              </p>
            )}

            {phase === "done" && result && (
              <>
                <h3 className="text-base font-semibold flex items-center gap-2">
                  {result.status === "ok" ? (
                    <>
                      <CheckCircle2 size={16} className="text-emerald-400" aria-hidden="true" />
                      AI services restarted
                    </>
                  ) : (
                    <>
                      <AlertTriangle size={16} className="text-amber-400" aria-hidden="true" />
                      Could not restart AI services
                    </>
                  )}
                </h3>
                <ul className="space-y-1.5 text-sm" aria-label="Restart results">
                  {result.results.map((r) => (
                    <li key={r.unit} className="flex items-start justify-between gap-3">
                      <span className="text-shell-text-secondary">{unitLabel(r.unit)}</span>
                      {r.ok ? (
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 shrink-0">
                          restarted
                        </span>
                      ) : (
                        <span className="text-[11px] text-amber-300/90 text-right">
                          {r.detail ?? "failed"}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {result.status !== "ok" && (
                  <p className="text-xs text-shell-text-tertiary">
                    Some services could not be restarted automatically — they may need permissions
                    this device has not granted yet.
                  </p>
                )}
                <div className="flex justify-end">
                  <Button variant="outline" size="sm" onClick={close}>
                    Close
                  </Button>
                </div>
              </>
            )}

            {phase === "error" && (
              <>
                <h3 className="text-base font-semibold flex items-center gap-2">
                  <AlertTriangle size={16} className="text-red-400" aria-hidden="true" />
                  Restart failed
                </h3>
                <p className="text-sm text-shell-text-secondary">{error}</p>
                <div className="flex justify-end">
                  <Button variant="outline" size="sm" onClick={close}>
                    Close
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
