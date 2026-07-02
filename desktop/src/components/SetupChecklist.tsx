import { useState, useEffect } from "react";
import { CheckCircle2, Circle, ChevronRight, X } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";

interface SetupStatus {
  account: boolean;
  has_provider: boolean;
  taos_model_set: boolean;
  has_agent: boolean;
  memory_enabled: boolean;
  npu_present?: boolean;
  npu_backend_running?: boolean;
  dismissed: boolean;
  complete: boolean;
}

interface Step {
  key: keyof SetupStatus;
  label: string;
  detail: string;
  appId?: string;
}

const STEPS: Step[] = [
  {
    key: "account",
    label: "Create your account",
    detail: "Done at sign-up",
  },
  {
    key: "has_provider",
    label: "Add a provider",
    detail: "Connect a cloud API key or local model server",
    appId: "providers",
  },
  {
    key: "taos_model_set",
    label: "Choose a model for the taOS agent",
    detail: "Pick the model your taOS agent will use",
    appId: "models",
  },
  {
    key: "has_agent",
    label: "Deploy your first agent",
    detail: "Deploy an AI agent (Hermes recommended)",
    appId: "agents",
  },
  {
    key: "memory_enabled",
    label: "Set up memory",
    detail: "taOSmd memory is recommended and on by default",
    appId: "memory",
  },
];

// Shown only when the board has a Rockchip NPU (#1535): the backend that
// serves on-device models is a Store install, and without this step nothing
// in the setup flow ever surfaces it.
const NPU_STEP: Step = {
  key: "npu_backend_running",
  label: "Install the NPU backend",
  detail: "Install rkllama from the Store to run models on this device's NPU",
  appId: "store",
};

export function SetupChecklist({ onDismissed }: { onDismissed?: () => void }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [dismissing, setDismissing] = useState(false);
  const openWindow = useProcessStore((s) => s.openWindow);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/setup/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: SetupStatus | null) => {
        if (!cancelled && data) setStatus(data);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const handleDismiss = async () => {
    setDismissing(true);
    try {
      await fetch("/api/setup/dismiss", { method: "POST" });
    } catch { /* ignore */ }
    onDismissed?.();
  };

  const handleStep = (step: Step) => {
    if (!step.appId) return;
    const app = getApp(step.appId);
    if (app) openWindow(step.appId, app.defaultSize);
  };

  if (!status || status.dismissed) return null;
  // complete covers the two core steps only; on an NPU board the backend
  // install still deserves a surface until it is running (or the user
  // dismisses the checklist), otherwise it would never be seen (#1535).
  const npuOutstanding = Boolean(status.npu_present) && !status.npu_backend_running;
  if (status.complete && !npuOutstanding) return null;

  const steps = status.npu_present ? [...STEPS, NPU_STEP] : STEPS;
  const doneCount = steps.filter((s) => Boolean(status[s.key])).length;

  return (
    <div className="border-b border-white/10">
      {/* Checklist header */}
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-shell-text">Get started</span>
          <span className="text-[10px] text-shell-text-tertiary bg-white/5 rounded-full px-1.5 py-0.5">
            {doneCount}/{steps.length}
          </span>
        </div>
        <button
          onClick={handleDismiss}
          disabled={dismissing}
          className="p-0.5 rounded hover:bg-white/10 text-shell-text-tertiary"
          aria-label="Dismiss setup checklist"
          title="Dismiss"
        >
          <X size={12} />
        </button>
      </div>

      {/* Steps */}
      <ul role="list" className="pb-2">
        {steps.map((step) => {
          const done = Boolean(status[step.key]);
          return (
            <li key={step.key}>
              <button
                onClick={() => !done && handleStep(step)}
                disabled={done || !step.appId}
                className={`w-full text-left flex items-center gap-2.5 px-4 py-2 hover:bg-white/5 transition-colors ${
                  done ? "cursor-default" : step.appId ? "cursor-pointer" : "cursor-default"
                }`}
                aria-label={done ? `${step.label} — complete` : step.label}
              >
                {done ? (
                  <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
                ) : (
                  <Circle size={14} className="text-shell-text-tertiary shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <p className={`text-xs ${done ? "line-through text-shell-text-tertiary" : "text-shell-text"}`}>
                    {step.label}
                  </p>
                  {!done && (
                    <p className="text-[10px] text-shell-text-tertiary truncate">{step.detail}</p>
                  )}
                </div>
                {!done && step.appId && (
                  <ChevronRight size={12} className="text-shell-text-tertiary shrink-0" />
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
