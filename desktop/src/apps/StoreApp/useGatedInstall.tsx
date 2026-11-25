import { useCallback, useState, type ReactNode } from "react";
import { LicenseAcceptDialog } from "./LicenseAcceptDialog";
import { CountryPromptDialog } from "./CountryPromptDialog";

/** What the install route said (412 bodies carry the licence step to show). */
interface LicenseGate { licenseName: string; name: string; text: string }

export type InstallOutcome = "ok" | "gated" | "error";

/**
 * The install request with the licence and country steps, for surfaces that do
 * not own the full desktop card (the phone Store). Same endpoint and same
 * dialogs as the desktop card: a 412 needs_license_acceptance opens the licence
 * dialog, a 412 needs_country opens the country dialog, and only the person's
 * Agree tap re-posts with accepted: true.
 */
export function useGatedInstall(opts: {
  appId: string;
  appName: string;
  target: string;
  onInstalled: (id: string) => void;
}) {
  const { appId, appName, target, onInstalled } = opts;
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [licenseGate, setLicenseGate] = useState<LicenseGate | null>(null);
  const [countryGate, setCountryGate] = useState<{ detail: string } | null>(null);

  const post = useCallback(async (accepted: boolean, country?: string): Promise<InstallOutcome> => {
    const body: Record<string, unknown> = { app_id: appId, target_remote: target };
    if (accepted) body.accepted = true;
    if (country) body.country = country;
    const res = await fetch("/api/store/install-v2", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 412) {
      try {
        const gate = await res.json();
        if (gate?.needs_country) {
          setCountryGate({ detail: String(gate.detail ?? "Choose your country to continue.") });
          return "gated";
        }
        if (gate?.needs_license_acceptance) {
          setLicenseGate({
            licenseName: String(gate.license_name ?? gate.weights_license ?? ""),
            name: String(gate.name ?? appName),
            text: String(gate.text ?? ""),
          });
          return "gated";
        }
      } catch { /* fall through to the generic failure */ }
    }
    if (!res.ok) {
      let msg = `Install failed (${res.status})`;
      try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ }
      setError(msg);
      return "error";
    }
    onInstalled(appId);
    return "ok";
  }, [appId, appName, target, onInstalled]);

  const run = useCallback(async (accepted: boolean, country?: string) => {
    setBusy(true);
    setFailed(false);
    setError(null);
    try {
      const outcome = await post(accepted, country);
      if (outcome === "error") setFailed(true);
      if (outcome !== "gated") { setLicenseGate(null); setCountryGate(null); }
    } catch {
      setFailed(true);
      setLicenseGate(null);
      setCountryGate(null);
    }
    setBusy(false);
  }, [post]);

  const start = useCallback(() => run(false), [run]);

  const decline = () => { setLicenseGate(null); setCountryGate(null); setBusy(false); };

  const dialogs: ReactNode = (
    <>
      {countryGate && (
        <CountryPromptDialog
          appName={appName}
          detail={countryGate.detail}
          submitting={submitting}
          error={error}
          onSave={async (code) => {
            setSubmitting(true);
            setCountryGate(null);
            await run(false, code);
            setSubmitting(false);
          }}
          onDecline={decline}
        />
      )}
      {licenseGate && (
        <LicenseAcceptDialog
          appName={licenseGate.name}
          weightsLicense={licenseGate.licenseName}
          text={licenseGate.text}
          submitting={submitting}
          error={error}
          onAccept={async () => {
            setSubmitting(true);
            await run(true);
            setSubmitting(false);
          }}
          onDecline={decline}
        />
      )}
    </>
  );

  // A gate is waiting on the person, so the pill is not "busy" and not "failed".
  const waiting = licenseGate !== null || countryGate !== null;
  return { busy: busy && !waiting, failed, start, dialogs };
}
