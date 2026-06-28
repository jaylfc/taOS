import { useState, useEffect, useCallback } from "react";
import { ScrollText, RefreshCw } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { safeFetch } from "@/apps/SettingsApp/_shared";

interface ClientLog {
  id: string;
  level: string;
  message: string;
  source: string;
  url: string;
  stack: string;
  created_at: string;
}

const LEVELS = ["all", "fatal", "error", "warn", "info", "debug"] as const;
type LevelFilter = (typeof LEVELS)[number];

const LEVEL_TONE: Record<string, string> = {
  fatal: "text-red-400",
  error: "text-red-400",
  warn: "text-amber-400",
  info: "text-sky-400",
  debug: "text-shell-text-tertiary",
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Read-only viewer for the errors and crashes captured from this device
 *  (GET /api/client-logs, admin only). The point is to chase a bug you hit in
 *  an app without opening a console -- a PWA has none. */
export function LogsSection() {
  const [logs, setLogs] = useState<ClientLog[]>([]);
  const [level, setLevel] = useState<LevelFilter>("all");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const q = level === "all" ? "" : `?level=${level}`;
    const data = await safeFetch<{ items: ClientLog[] }>(
      `/api/client-logs${q}`, { items: [] },
    );
    setLogs(Array.isArray(data.items) ? data.items : []);
    setLoading(false);
  }, [level]);

  useEffect(() => { load(); }, [load]);

  return (
    <section aria-label="Logs">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg font-semibold">Logs</h2>
        <Button variant="outline" size="sm" onClick={load} aria-label="Refresh logs">
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>
      <p className="text-sm text-shell-text-tertiary mb-4">
        Errors and crashes captured from this device, newest first. Use these to chase a bug you hit in an app.
      </p>

      <div className="flex gap-2 mb-3 flex-wrap" role="group" aria-label="Filter by level">
        {LEVELS.map((l) => (
          <Button
            key={l}
            variant={level === l ? "secondary" : "outline"}
            size="sm"
            onClick={() => setLevel(l)}
            aria-pressed={level === l}
          >
            {l}
          </Button>
        ))}
      </div>

      {loading && <p className="text-sm text-shell-text-tertiary">Loading...</p>}

      {!loading && logs.length === 0 && (
        <Card className="p-4">
          <p className="text-sm flex items-center gap-2 text-shell-text-tertiary">
            <ScrollText size={14} /> No logs captured yet.
          </p>
        </Card>
      )}

      {!loading && logs.length > 0 && (
        <div className="space-y-2">
          {logs.map((log) => (
            <Card key={log.id} className="p-3">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs font-medium uppercase ${LEVEL_TONE[log.level] ?? ""}`}>
                  {log.level}
                </span>
                <span className="text-xs text-shell-text-tertiary">{formatTime(log.created_at)}</span>
              </div>
              <p className="text-sm mt-1 break-words">{log.message}</p>
              {(log.source || log.url) && (
                <p className="text-xs text-shell-text-tertiary mt-0.5 truncate">
                  {[log.source, log.url].filter(Boolean).join(" · ")}
                </p>
              )}
              {log.stack && (
                <details className="mt-1">
                  <summary className="text-xs text-shell-text-tertiary cursor-pointer">Stack</summary>
                  <pre className="text-xs text-shell-text-tertiary mt-1 whitespace-pre-wrap break-words">{log.stack}</pre>
                </details>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
