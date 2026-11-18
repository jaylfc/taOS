import { useState } from "react";
import { MonitorSmartphone } from "lucide-react";
import { registerThisDevice } from "@/lib/device-roster";
import { OobePage } from "../OobeShell";

/**
 * Shown only after a taOS account sign-in: this device becomes the first one
 * on the account. A second device's "Approve this device" screen comes with
 * the roster service (docs/proposals/account-device-sync.md).
 */
export function DeviceStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    const result = await registerThisDevice();
    setBusy(false);
    if (result.status === "error") {
      setError(result.message);
      return;
    }
    onNext();
  }

  return (
    <OobePage
      icon={<MonitorSmartphone size={56} strokeWidth={1.5} aria-hidden="true" className="oobe-icon" />}
      title="This is your first taOS device"
      subtitle="Other devices you sign in to with this account will ask you to approve them here, so they share your agents, memory and settings."
      onSubmit={() => void submit()}
      footer={
        <>
          <button type="submit" className="oobe-primary" disabled={busy} aria-busy={busy}>
            {busy ? "Please wait..." : "Continue"}
          </button>
          {error && (
            <button type="button" className="oobe-secondary" onClick={onSkip}>
              Set Up Later
            </button>
          )}
        </>
      }
    >
      {error && (
        <p className="oobe-error" role="alert">
          {error}
        </p>
      )}
    </OobePage>
  );
}
