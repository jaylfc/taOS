import { useState, useEffect } from "react";
import { CheckCircle2, Circle, ChevronRight, Loader2, X } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { fetchAccount, type AccountState } from "@/lib/account-client";

// accel identifies the accelerator this device's default local-LLM-backend
// step targets. "none" (or absent) means no one-tap backend step is shown
// (e.g. Intel Arc / Mali have no one-tap backend yet).
type Accel = "rknpu" | "cuda" | "rocm" | "metal" | "cpu" | "none";

interface SetupStatus {
  account: boolean;
  has_provider: boolean;
  taos_model_set: boolean;
  has_agent: boolean;
  memory_enabled: boolean;
  npu_present?: boolean;
  npu_backend_running?: boolean;
  accel?: Accel;
  default_backend_running?: boolean;
  dismissed: boolean;
  complete: boolean;
}

// "cloud_account" isn't part of the backend status payload -- it self-ticks
// from the taos.my account state (see CLOUD_ACCOUNT_STEP below).
interface Step {
  key: keyof SetupStatus | "cloud_account";
  label: string;
  detail: string;
  appId?: string;
  appProps?: Record<string, unknown>;
}

const STEPS: Step[] = [
  {
    key: "account",
    label: "Create your account",
    detail: "Done at sign-up",
  },
  {
    key: "cloud_account",
    label: "Sign in to your taOS account (optional)",
    detail:
      "One account for taOSgo remote access, app sharing, and reserving your taOS username for a future website and socials.",
    appId: "settings",
    appProps: { section: "account" },
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

// Platform-aware "install a local model backend" step. Originally
// Rockchip-only (#1535: rkllama / rk-llama.cpp), generalized to every other
// accelerator via the llama.cpp router-mode server (locked product
// decision: MIT, one binary, NVIDIA CUDA / AMD ROCm / Apple Silicon /
// x86 CPU-only). Shown whenever this device has a supported accel and the
// backend isn't already running; hidden entirely when accel is "none"
// (e.g. Intel Arc / Mali have no one-tap backend yet). Tapping it is a
// one-tap install (POST /api/setup/install-backend); "Open in Store"
// stays as a secondary, manual escape hatch.
interface BackendStepCopy {
  label: string;
  detail: string;
  buttonLabel: string;
  buttonBusyLabel: string;
  errorText: string;
}

function backendStepCopyFor(accel: Accel): BackendStepCopy {
  if (accel === "rknpu") {
    // Rockchip keeps its pre-#1597 copy verbatim.
    return {
      label: "Install the NPU backend",
      detail: "Installs the Rockchip NPU runtime so models run on this device's NPU.",
      buttonLabel: "Install NPU backend",
      buttonBusyLabel: "Installing NPU backend...",
      errorText: "Couldn't install the NPU backend. Try again.",
    };
  }
  const device = accel === "cpu" ? "CPU" : "GPU";
  return {
    label: "Install a local model backend",
    detail: `Runs chat and memory models locally on this device's ${device}.`,
    buttonLabel: "Install llama.cpp server",
    buttonBusyLabel: "Installing llama.cpp server...",
    errorText: "Couldn't install the local model backend. Try again.",
  };
}

const BACKEND_STEP_BASE: Step = {
  key: "default_backend_running",
  label: "",
  detail: "",
  appId: "store",
};

// How often to re-poll /api/setup/status while a backend install is in
// flight, so the step ticks on its own once the backend comes up.
const BACKEND_POLL_MS = 3000;

export function SetupChecklist({ onDismissed }: { onDismissed?: () => void }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [dismissing, setDismissing] = useState(false);
  // The taos.my cloud account is optional and separate from the local setup
  // status endpoint; it degrades to "unavailable" rather than throwing when
  // the account service can't be reached, so onboarding still works offline.
  const [cloudAccount, setCloudAccount] = useState<AccountState>({ kind: "loading" });
  const [backendInstalling, setBackendInstalling] = useState(false);
  const [backendError, setBackendError] = useState<string | null>(null);
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

  useEffect(() => {
    let cancelled = false;
    fetchAccount().then((state) => {
      if (!cancelled) setCloudAccount(state);
    });
    return () => { cancelled = true; };
  }, []);

  // While a backend install is in flight, re-poll status so the step ticks
  // over to done on its own the moment default_backend_running flips true —
  // no manual refresh needed.
  useEffect(() => {
    if (!backendInstalling) return;
    let cancelled = false;
    const id = setInterval(() => {
      fetch("/api/setup/status")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: SetupStatus | null) => {
          if (cancelled || !data) return;
          setStatus(data);
          if (data.default_backend_running) setBackendInstalling(false);
        })
        .catch(() => {});
    }, BACKEND_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [backendInstalling]);

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
    if (app) openWindow(step.appId, app.defaultSize, step.appProps);
  };

  const cloudSignedIn = cloudAccount.kind === "signed-in";
  // default_backend_running falls back to the legacy npu_backend_running
  // field so a payload that only sends the pre-generalization shape (or an
  // older-shaped test mock) still reflects the real running state.
  const isDone = (step: Step) => {
    if (step.key === "cloud_account") return cloudSignedIn;
    if (step.key === "default_backend_running") {
      return Boolean(status?.default_backend_running ?? status?.npu_backend_running ?? false);
    }
    return Boolean(status?.[step.key]);
  };

  // Primary one-tap action for the backend step: trigger the server-side
  // install (rkllama + rk-llama.cpp on Rockchip, llama.cpp server
  // everywhere else) rather than sending the user to the Store to hunt for
  // it. Stays "installing" until the poll above confirms the backend is
  // actually answering — the tap only starts the install, it doesn't finish it.
  const handleInstallBackend = async () => {
    setBackendError(null);
    setBackendInstalling(true);
    try {
      const res = await fetch("/api/setup/install-backend", { method: "POST" });
      if (!res.ok) throw new Error(`install-backend: ${res.status}`);
    } catch {
      setBackendInstalling(false);
      setBackendError(backendStepCopyFor(status?.accel ?? "none").errorText);
    }
  };

  if (!status || status.dismissed) return null;
  const accel = status.accel ?? (status.npu_present ? "rknpu" : "none");
  const showBackendStep = accel !== "none";
  // complete covers the two core steps only; a device with a supported
  // accel still deserves the backend-install surface until it is running
  // (or the user dismisses the checklist), otherwise it would never be
  // seen (#1535).
  const backendOutstanding = showBackendStep && !isDone(BACKEND_STEP_BASE);
  if (status.complete && !backendOutstanding) return null;

  const backendCopy = backendStepCopyFor(accel);
  const backendStep: Step = { ...BACKEND_STEP_BASE, label: backendCopy.label, detail: backendCopy.detail };
  const steps = showBackendStep ? [...STEPS, backendStep] : STEPS;
  const doneCount = steps.filter((s) => isDone(s)).length;
  // Signed in but hasn't claimed a username yet -- surfaced as a nudge, not
  // a blocker; omitted when signed out or once a handle exists.
  const showHandleHint =
    cloudAccount.kind === "signed-in" && !cloudAccount.account.handle;

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
          const done = isDone(step);
          const isBackendStep = step.key === "default_backend_running";

          // The backend step gets a one-tap install action + a secondary
          // "Open in Store" link instead of a single row-as-button; every
          // other step (and this one once done) keeps the plain
          // tap-to-open row.
          if (isBackendStep && !done) {
            return (
              <li key={step.key}>
                <div className="flex items-center gap-2.5 px-4 py-2">
                  <Circle size={14} className="text-shell-text-tertiary shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-shell-text">{step.label}</p>
                    <p className={`text-[10px] truncate ${backendError ? "text-red-400" : "text-shell-text-tertiary"}`}>
                      {backendError ?? step.detail}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={handleInstallBackend}
                      disabled={backendInstalling}
                      className="flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-60 disabled:cursor-default"
                    >
                      {backendInstalling && <Loader2 size={10} className="animate-spin" />}
                      {backendInstalling ? backendCopy.buttonBusyLabel : backendCopy.buttonLabel}
                    </button>
                    <button
                      onClick={() => handleStep(step)}
                      className="text-[10px] text-shell-text-tertiary hover:text-shell-text underline underline-offset-2"
                    >
                      Open in Store
                    </button>
                  </div>
                </div>
              </li>
            );
          }

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
                  {step.key === "cloud_account" && showHandleHint && (
                    <p className="text-[10px] text-shell-text-tertiary/70 truncate">
                      Tip: reserve your taOS username in Settings before someone else takes it.
                    </p>
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
