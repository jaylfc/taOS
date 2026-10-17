/**
 * Benchmark panel for one worker, used by the Cluster app.
 *
 * Reads `GET /api/workers/{name}/benchmark` (newest row per capability +
 * model, the full run history, and any queued run) and offers the
 * "Re-run benchmarks" button, which queues a run via
 * `POST /api/workers/{name}/benchmark`.
 *
 * The button reports "queued" rather than "running" on purpose: the worker
 * agent is a poller, so a queued run is handed to it on its next heartbeat
 * (about five seconds) and the results land a minute or two later. Nothing
 * re-runs on its own after that -- the user decides when a worker is busy.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Play } from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import { formatRelativeSeconds } from "@/lib/cluster";
import { withCsrf } from "@/lib/csrf";

/** One recorded measurement (a row of `data/benchmarks.db`). */
export interface BenchmarkRow {
  worker_id?: string;
  capability: string;
  model: string;
  metric: string;
  value: number | null;
  unit?: string | null;
  status: string;
  measured_at: number;
  first_join?: boolean;
}

/** A manual run queued on the controller, waiting for the worker heartbeat. */
export interface BenchmarkPending {
  worker_id: string;
  requested_at: number;
  force?: boolean;
}

/**
 * How long a queued run may sit before the panel stops calling it "pending".
 * The worker picks it up on its next heartbeat (about five seconds), so a
 * queue entry older than this is stuck rather than imminent.
 */
export const PENDING_STALE_AFTER_SECONDS = 300;

export interface BenchmarkPayload {
  latest?: BenchmarkRow[];
  history?: BenchmarkRow[];
  pending?: BenchmarkPending | null;
}

/** Render a measurement, or its status when the run did not produce a value. */
export function formatBenchmarkValue(
  value: number | null | undefined,
  unit?: string | null,
): string {
  if (value === null || value === undefined) return "\u2014";
  const rounded = Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2);
  return unit ? `${rounded} ${unit}` : rounded;
}

export function WorkerBenchmarksSection({ workerName }: { workerName: string }) {
  const [data, setData] = useState<BenchmarkPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A slow GET for the previous worker must not land after the user switched
  // workers -- that would render one worker's rows under another's name.
  const requestSeq = useRef(0);

  const fetchBenchmarks = useCallback(async () => {
    const seq = ++requestSeq.current;
    try {
      const res = await fetch(`/api/workers/${encodeURIComponent(workerName)}/benchmark`, {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const body = (await res.json()) as BenchmarkPayload;
        if (seq === requestSeq.current) setData(body);
      }
    } catch {
      /* transient -- the panel keeps the last good snapshot */
    }
    if (seq === requestSeq.current) setLoading(false);
  }, [workerName]);

  // Reset the panel when the selected worker changes so one worker's notice,
  // error and rows never render under another worker's name.
  useEffect(() => {
    requestSeq.current += 1;
    setData(null);
    setNotice(null);
    setError(null);
    setLoading(true);
  }, [workerName]);

  useEffect(() => {
    fetchBenchmarks();
    const interval = setInterval(fetchBenchmarks, 15_000);
    return () => clearInterval(interval);
  }, [fetchBenchmarks]);

  const pending = data?.pending ?? null;

  const handleReRun = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const res = await fetch(
        `/api/workers/${encodeURIComponent(workerName)}/benchmark`,
        withCsrf({
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          // A click while a run is already queued is a deliberate "again":
          // replace the queued run instead of failing the 409 guard.
          body: JSON.stringify({ force: Boolean(pending) }),
        }),
      );
      if (res.status === 202) {
        setNotice("Benchmark queued \u2014 the worker starts it on its next heartbeat.");
      } else if (res.status === 409) {
        const body = await res.json().catch(() => null);
        setError(String(body?.error ?? "A benchmark run is already queued for this worker."));
      } else if (res.status === 404) {
        setError("This worker is no longer registered with the controller.");
      } else if (res.status === 403) {
        setError("Only an admin can queue a benchmark run.");
      } else {
        setError(`Could not queue a benchmark run (HTTP ${res.status}).`);
      }
    } catch {
      setError("Could not reach the controller.");
    }
    setBusy(false);
    await fetchBenchmarks();
  }, [fetchBenchmarks, pending, workerName]);

  const rows = data?.latest ?? [];
  const runCount = data?.history?.length ?? 0;
  // The worker normally picks a queued run up within a heartbeat or two. Past
  // this the queue entry is more likely stuck (worker busy or wedged) than
  // pending, and the user should be told to re-click rather than wait.
  const pendingStale =
    pending !== null &&
    Date.now() / 1000 - pending.requested_at > PENDING_STALE_AFTER_SECONDS;
  const lastRun = useMemo(() => {
    const times = (data?.history ?? []).map((h) => h.measured_at).filter((t) => typeof t === "number");
    return times.length > 0 ? Math.max(...times) : null;
  }, [data]);

  return (
    <Card className="p-4">
      <CardContent className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <Activity size={14} className="text-emerald-400" />
            <h3 className="text-xs font-semibold text-shell-text">Benchmarks</h3>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleReRun}
            disabled={busy}
            aria-label={`Re-run benchmarks on ${workerName}`}
            title="Queue a benchmark run. The worker starts it on its next heartbeat — nothing re-runs automatically."
          >
            <Play size={13} />
            {busy ? "Queueing\u2026" : "Re-run benchmarks"}
          </Button>
        </div>

        {pending && (
          <p className="text-[11px] text-amber-300/90 mb-2" role="status">
            {pendingStale
              ? `A run was queued ${formatRelativeSeconds(pending.requested_at)} and the worker has not picked it up \u2014 click Re-run benchmarks to replace it.`
              : `A run was queued ${formatRelativeSeconds(pending.requested_at)}; it starts on this worker's next heartbeat.`}
          </p>
        )}

        {loading && rows.length === 0 ? (
          <p className="text-[11px] text-shell-text-tertiary">
            Loading benchmark results{"\u2026"}
          </p>
        ) : rows.length === 0 ? (
          <p className="text-[11px] text-shell-text-tertiary">
            No benchmark results yet. The first run happens automatically when a
            worker joins the cluster; use Re-run benchmarks to measure it again.
          </p>
        ) : (
          <div className="space-y-2">
            {rows.map((row) => (
              <div
                key={`${row.capability}\u0000${row.model}\u0000${row.metric}`}
                className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/5 pb-1.5 last:border-0"
              >
                <div className="min-w-0">
                  <p className="text-[11px] text-shell-text">
                    {row.metric}
                    <span className="text-shell-text-tertiary">{" \u00b7 "}{row.capability}</span>
                  </p>
                  <p className="text-[10px] text-shell-text-tertiary font-mono break-all">
                    {row.model}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-[12px] text-shell-text font-mono">
                    {row.status === "ok" ? formatBenchmarkValue(row.value, row.unit) : row.status}
                  </p>
                  <p className="text-[10px] text-shell-text-tertiary">
                    {formatRelativeSeconds(row.measured_at)}
                    {row.first_join ? " \u00b7 first run" : ""}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}

        {notice && (
          <p className="text-[11px] text-emerald-300/90 mt-2" role="status">
            {notice}
          </p>
        )}
        {error && (
          <p className="text-[11px] text-red-300/90 mt-2" role="alert">
            {error}
          </p>
        )}

        {runCount > 0 && (
          <p className="text-[10px] text-shell-text-tertiary mt-2">
            {runCount} recorded {runCount === 1 ? "measurement" : "measurements"}
            {lastRun ? ` \u00b7 last run ${formatRelativeSeconds(lastRun)}` : ""}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
