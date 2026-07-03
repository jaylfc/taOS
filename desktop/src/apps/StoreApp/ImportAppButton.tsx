import { useCallback, useRef, useState } from "react";
import { Upload, Loader2 } from "lucide-react";
import {
  installUserspaceApp,
  fetchUserspaceAppRow,
  USERSPACE_APPS_CHANGED,
  type InstallResult,
} from "@/lib/userspace-apps";
import { emitAppEvent } from "@/lib/app-event-bus";
import { PermissionConsent } from "./PermissionConsent";

/** Import a .taosapp package from disk and install it as a userspace app.
 * When the install requests new permissions, the consent dialog shows before
 * anything sensitive is granted -- the app itself is already installed at
 * that point (with only its free capabilities usable) so it can never be
 * blocked on a decision the user hasn't made yet. */
export function ImportAppButton() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consent, setConsent] = useState<{
    appId: string;
    appName: string;
    requested: string[];
    alreadyGranted: string[];
  } | null>(null);

  const handleFile = useCallback(async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const result: InstallResult = await installUserspaceApp(file);
      // The row is now enabled and visible regardless of consent -- only its
      // gated capabilities are withheld until the user answers below.
      emitAppEvent(USERSPACE_APPS_CHANGED);
      if (result.needs_consent) {
        const row = await fetchUserspaceAppRow(result.app_id);
        setConsent({
          appId: result.app_id,
          appName: row?.name ?? result.app_id,
          requested: result.new_permissions,
          alreadyGranted: row?.permissions_granted ?? [],
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Install failed");
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".taosapp,.zip"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void handleFile(file);
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        className="flex items-center gap-1.5 h-8 px-3 rounded-lg text-[12px] font-semibold border border-shell-border text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.04] transition-colors disabled:opacity-60"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
        Import app package
      </button>
      {error && (
        <div role="alert" className="mt-2 text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
          {error}
        </div>
      )}
      {consent && (
        <PermissionConsent
          appId={consent.appId}
          appName={consent.appName}
          requested={consent.requested}
          alreadyGranted={consent.alreadyGranted}
          onAllow={() => {
            emitAppEvent(USERSPACE_APPS_CHANGED);
            setConsent(null);
          }}
          onDeny={() => setConsent(null)}
        />
      )}
    </>
  );
}

export default ImportAppButton;
