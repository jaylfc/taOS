import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

interface StoreStatus {
  state: string;
  can_serve: boolean;
  message: string;
  next_step: string;
  convert_available: boolean;
}

interface ConvertRecord {
  state: string;
  ok?: boolean;
  message: string;
  backup_path?: string | null;
}

interface MemoryStoreData {
  status: StoreStatus | null;
  converting: boolean;
  last_convert: ConvertRecord | null;
}

const POLL_MS = 2000;

/** Settings -> Memory -> Memory store.
 *
 *  GET /api/memory-store reports what the taOSmd store check found and the last
 *  conversion; POST /api/memory-store/convert {confirm: true} starts the optional,
 *  one-way vector conversion in the background. The owner's confirmation here is
 *  the consent; an agent asking for the same thing raises a card in Decisions.
 *  The layout is one column, so it reads the same on a phone.
 */
export function MemoryStorePanel() {
  const [data, setData] = useState<MemoryStoreData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/memory-store", { credentials: "include" });
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || !body || typeof body.converting !== "boolean") {
        setError("Could not load the memory store status.");
        return;
      }
      setError(null);
      setData(body as MemoryStoreData);
    } catch {
      if (mounted.current) setError("Could not reach taOS.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const running = !!data?.converting || starting;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => { void load(); }, POLL_MS);
    return () => clearInterval(t);
  }, [running, load]);

  const convert = async () => {
    setStarting(true);
    setError(null);
    try {
      const res = await fetch("/api/memory-store/convert", withCsrf({
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok) {
        setError(body?.error || body?.detail || `Could not start the conversion (${res.status}).`);
      } else if (body?.state === "consent_requested") {
        setError("Approval is needed first. Open Decisions to approve it.");
      }
      setConfirming(false);
      await load();
    } catch {
      if (mounted.current) setError("Could not reach taOS.");
    } finally {
      if (mounted.current) setStarting(false);
    }
  };

  if (!data) {
    return (
      <section aria-label="Memory store" className="mt-6">
        <h3 className="text-sm font-medium mb-2">Memory store</h3>
        {error
          ? <p role="alert" className="text-xs text-amber-400 flex items-center gap-1.5"><AlertCircle size={12} /> {error}</p>
          : <p className="text-xs text-shell-text-tertiary">Loading...</p>}
      </section>
    );
  }

  const status = data.status;
  const last = data.last_convert;
  const showLast = last && last.state !== "running";

  return (
    <section aria-label="Memory store" className="mt-6">
      <h3 className="text-sm font-medium mb-2">Memory store</h3>
      {error && (
        <p role="alert" className="mb-2 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      <Card className="p-4 flex flex-col gap-3">
        <p className="text-xs text-shell-text-secondary flex items-start gap-1.5">
          {status?.state === "current" || status?.state === "old"
            ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
            : <AlertCircle size={14} className="mt-0.5 shrink-0" />}
          <span>{status ? status.message : "The memory store has not been checked yet."}</span>
        </p>
        {data.converting && (
          <p role="status" className="text-xs text-shell-text-tertiary">
            Converting. Your memories stay safe: taOSmd keeps a backup and puts everything back if anything fails.
          </p>
        )}
        {showLast && (
          <p role="status" className={`text-xs ${last.ok ? "text-shell-text-secondary" : "text-amber-400"}`}>
            {last.message}
            {last.backup_path ? ` Backup: ${last.backup_path}` : ""}
          </p>
        )}
        {status?.convert_available && !data.converting && !confirming && (
          <Button
            type="button"
            className="min-h-[44px] w-full sm:w-auto"
            onClick={() => setConfirming(true)}
          >
            Convert vectors...
          </Button>
        )}
        {confirming && !data.converting && (
          <div className="flex flex-col gap-2" role="group" aria-label="Confirm conversion">
            <p className="text-xs text-shell-text-secondary">
              This is optional and one way: older taOSmd releases cannot read the store afterwards.
              taOSmd takes a verified backup first and restores it if anything fails.
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <Button type="button" className="min-h-[44px]" disabled={starting} onClick={convert}>
                Convert now
              </Button>
              <Button type="button" variant="ghost" className="min-h-[44px]" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </Card>
    </section>
  );
}
