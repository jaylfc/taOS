import { useEffect, useRef, useState } from "react";
import { Cpu } from "lucide-react";
import { formatSize, pickOnDeviceModel, type CatalogModel, type OnDeviceChoice } from "../model";
import { OobePage } from "../OobeShell";

type Phase =
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "offer" }
  | { kind: "starting" }
  | { kind: "downloading"; percent: number }
  | { kind: "ready" }
  | { kind: "error"; message: string };

const POLL_MS = 1000;

/**
 * Offers one small chat model sized to this device and downloads it with
 * progress. The download runs on the server, so Continue is open while it
 * does; the personal assistant uses the model once it lands.
 */
export function ModelStep({
  onBack,
  onDone,
  onSkip,
}: {
  onBack: () => void;
  /** The user took the model (downloading or downloaded). */
  onDone: (choice: OnDeviceChoice) => void;
  onSkip: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [choice, setChoice] = useState<OnDeviceChoice | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const res = await fetch("/api/models", { credentials: "include" });
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        const models: CatalogModel[] = Array.isArray(data?.models) ? data.models : [];
        const pick = pickOnDeviceModel(models, String(data?.hardware_profile_id ?? ""));
        if (!mounted.current) return;
        setChoice(pick);
        setPhase(!pick ? { kind: "none" } : pick.downloaded ? { kind: "ready" } : { kind: "offer" });
      } catch {
        if (mounted.current) setPhase({ kind: "error", message: "Couldn't read the model list from taOS." });
      }
    })();
    return () => {
      mounted.current = false;
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  async function download() {
    if (!choice) return;
    setPhase({ kind: "starting" });
    try {
      const res = await fetch("/api/models/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ app_id: choice.app_id, variant_id: choice.variant_id }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.download_id) {
        throw new Error(data?.error || data?.detail || "The download could not be started.");
      }
      if (!mounted.current) return;
      setPhase({ kind: "downloading", percent: 0 });
      const id = String(data.download_id);
      timer.current = setInterval(() => void poll(id), POLL_MS);
      void poll(id);
    } catch (e) {
      if (mounted.current) setPhase({ kind: "error", message: (e as Error).message });
    }
  }

  async function poll(id: string) {
    try {
      const res = await fetch(`/api/models/downloads/${encodeURIComponent(id)}`, { credentials: "include" });
      const task = await res.json();
      if (!mounted.current || !task || typeof task !== "object") return;
      if (task.status === "complete") {
        if (timer.current) clearInterval(timer.current);
        setPhase({ kind: "ready" });
      } else if (task.status === "error") {
        if (timer.current) clearInterval(timer.current);
        setPhase({ kind: "error", message: task.error || "The download failed." });
      } else {
        setPhase({ kind: "downloading", percent: Number(task.percent) || 0 });
      }
    } catch {
      /* a missed poll is retried on the next tick */
    }
  }

  const taken = () => choice && onDone({ ...choice, downloaded: phase.kind === "ready" });

  let primary: { label: string; busy?: boolean; disabled?: boolean; act: () => void };
  switch (phase.kind) {
    case "loading":
      primary = { label: "Loading...", busy: true, disabled: true, act: () => {} };
      break;
    case "none":
      primary = { label: "Continue", act: onSkip };
      break;
    case "offer":
      primary = { label: "Download", act: () => void download() };
      break;
    case "starting":
      primary = { label: "Starting...", busy: true, disabled: true, act: () => {} };
      break;
    case "downloading":
    case "ready":
      primary = { label: "Continue", act: () => taken() };
      break;
    case "error":
      primary = { label: "Try Again", act: () => (choice ? void download() : window.location.reload()) };
      break;
  }

  const showSkip = phase.kind === "offer" || phase.kind === "error" || phase.kind === "loading";

  return (
    <OobePage
      icon={<Cpu size={56} strokeWidth={1.5} aria-hidden="true" className="oobe-icon" />}
      title="A model on this device"
      subtitle="A small AI model that runs right here, with no internet and nothing leaving the device."
      onBack={phase.kind === "starting" ? undefined : onBack}
      onSubmit={() => !primary.disabled && primary.act()}
      footer={
        <>
          <button
            type="submit"
            className="oobe-primary"
            disabled={primary.disabled}
            aria-busy={primary.busy || undefined}
          >
            {primary.label}
          </button>
          {showSkip && (
            <button type="button" className="oobe-secondary" onClick={onSkip}>
              Set Up Later
            </button>
          )}
        </>
      }
    >
      {phase.kind === "none" ? (
        <p className="oobe-status" role="status">
          None of the models in the catalog fit this device's memory comfortably. You can add one
          later in Models, or use a cloud model in the next step.
        </p>
      ) : (
        choice && (
          <div className="oobe-group">
            <div className="oobe-model">
              <span className="oobe-choice-text">
                <span className="oobe-choice-title">{choice.name}</span>
                <span className="oobe-choice-detail">
                  {phase.kind === "ready"
                    ? "Downloaded and ready"
                    : phase.kind === "downloading"
                      ? `Downloading, ${Math.round(phase.percent)}% of ${formatSize(choice.size_mb)}`
                      : `${formatSize(choice.size_mb)} download`}
                </span>
              </span>
            </div>
            {phase.kind === "downloading" && (
              <div
                className="oobe-meter"
                role="progressbar"
                aria-label={`Downloading ${choice.name}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(phase.percent)}
              >
                <span style={{ width: `${Math.max(2, phase.percent)}%` }} />
              </div>
            )}
          </div>
        )
      )}
      {phase.kind === "downloading" && (
        <p className="oobe-note">It keeps downloading if you continue.</p>
      )}
      {phase.kind === "error" && (
        <p className="oobe-error" role="alert">
          {phase.message}
        </p>
      )}
    </OobePage>
  );
}
