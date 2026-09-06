import { useState, useEffect, useCallback, useRef } from "react";
import { ScrollText, RefreshCw, Radio } from "lucide-react";
import { Button, Card, Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
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

/** GET /api/client-logs viewer: errors and crashes captured from this device
 *  (browser-side, via window.onerror / unhandledrejection). */
function ClientLogsTab() {
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
    <>
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm text-shell-text-tertiary">
          Errors and crashes captured from this device, newest first. Use these to chase a bug you hit in an app.
        </p>
        <Button variant="outline" size="sm" onClick={load} aria-label="Refresh logs">
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>

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
    </>
  );
}

interface SystemLogSource {
  id: string;
  label: string;
  kind: string;
  available: boolean;
}

interface SystemLogPage {
  source: string;
  lines: string[];
  cursor: string | null;
  error?: string;
}

const MAX_LIVE_LINES = 500;

/** GET/SSE viewer for one journald/file source from the system-logs API
 *  (controller, rkllama, qmd, LLM proxy) -- the operational logs a failed
 *  backend operation actually lands in, as opposed to the browser-only
 *  ClientLogsTab above. */
function SystemLogsTab({ source }: { source: SystemLogSource }) {
  const [lines, setLines] = useState<string[]>([]);
  const [liveLines, setLiveLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tailing, setTailing] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const data = await safeFetch<SystemLogPage | null>(
      `/api/system-logs/${source.id}?lines=200`, null,
    );
    if (data && Array.isArray(data.lines)) {
      setLines(data.lines);
      setError(data.error ?? null);
    } else {
      setLines([]);
      setError("Could not read this log source.");
    }
    setLiveLines([]);
    setLoading(false);
  }, [source.id]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    return () => eventSourceRef.current?.close();
  }, []);

  const toggleTail = useCallback(() => {
    if (tailing) {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setTailing(false);
      return;
    }
    const es = new EventSource(`/api/system-logs/${source.id}/stream`);
    es.onmessage = (msg) => {
      let payload: { line?: string; error?: string } | null;
      try {
        payload = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (!payload) return;
      if (payload.line) {
        setLiveLines((prev) => [payload.line as string, ...prev].slice(0, MAX_LIVE_LINES));
      } else if (payload.error) {
        setError(payload.error);
      }
    };
    es.onerror = () => {
      // Transient network errors: leave the connection to the browser's
      // built-in reconnect. Closing here would fight it.
    };
    eventSourceRef.current = es;
    setTailing(true);
  }, [tailing, source.id]);

  const displayLines = [...liveLines, ...lines];

  return (
    <>
      <div className="flex items-center justify-between mb-2 gap-2">
        <p className="text-sm text-shell-text-tertiary">
          Last 200 lines from {source.label}, newest first.
        </p>
        <div className="flex gap-2 shrink-0">
          <Button
            variant={tailing ? "secondary" : "outline"}
            size="sm"
            onClick={toggleTail}
            aria-pressed={tailing}
            aria-label={tailing ? `Stop live tail of ${source.label}` : `Live tail ${source.label}`}
          >
            <Radio size={14} className={tailing ? "animate-pulse text-emerald-400" : ""} /> Live
          </Button>
          <Button variant="outline" size="sm" onClick={load} aria-label={`Refresh ${source.label} logs`}>
            <RefreshCw size={14} /> Refresh
          </Button>
        </div>
      </div>

      {loading && <p className="text-sm text-shell-text-tertiary">Loading...</p>}

      {!loading && error && displayLines.length === 0 && (
        <Card className="p-4">
          <p role="alert" className="text-sm flex items-center gap-2 text-amber-400">
            <ScrollText size={14} /> {error}
          </p>
        </Card>
      )}

      {!loading && !error && displayLines.length === 0 && (
        <Card className="p-4">
          <p className="text-sm flex items-center gap-2 text-shell-text-tertiary">
            <ScrollText size={14} /> No log lines yet.
          </p>
        </Card>
      )}

      {displayLines.length > 0 && (
        <Card className="p-3 max-h-[28rem] overflow-y-auto">
          <pre className="text-xs font-mono whitespace-pre-wrap break-words text-shell-text-secondary">
            {displayLines.join("\n")}
          </pre>
        </Card>
      )}
    </>
  );
}

/** Log viewer for Settings -- browser-side client errors plus, on installs
 *  where the system-logs API (controller/rkllama/qmd/LLM-proxy) is available,
 *  the operator logs a failed backend operation actually lands in. */
export function LogsSection() {
  const [sources, setSources] = useState<SystemLogSource[]>([]);

  useEffect(() => {
    let cancelled = false;
    safeFetch<SystemLogSource[]>("/api/system-logs/sources", []).then((data) => {
      if (!cancelled && Array.isArray(data)) {
        setSources(data.filter((s) => s.available && s.id !== "client"));
      }
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <section aria-label="Logs">
      <h2 className="text-lg font-semibold mb-2">Logs</h2>
      <Tabs defaultValue="client">
        <TabsList>
          <TabsTrigger value="client">Client</TabsTrigger>
          {sources.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>{s.label}</TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="client">
          <ClientLogsTab />
        </TabsContent>
        {sources.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <SystemLogsTab source={s} />
          </TabsContent>
        ))}
      </Tabs>
    </section>
  );
}
