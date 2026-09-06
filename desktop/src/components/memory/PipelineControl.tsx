import { useState } from "react";
import { Play, RefreshCw, AlertTriangle, CheckCircle, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { triggerCatalogIndex } from "@/lib/memory";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Tier = '1' | '2' | '3' | 'auto';

interface PipelineRun {
  id: string;
  timestamp: string;
  action: string;
  status: 'success' | 'error' | 'running';
  message?: string;
}

/* ------------------------------------------------------------------ */
/*  PipelineControl                                                    */
/* ------------------------------------------------------------------ */

export function PipelineControl() {
  const [specificDate, setSpecificDate] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [tier, setTier] = useState<Tier>('auto');
  const [crystallize, setCrystallize] = useState(false);
  const [showRebuildConfirm, setShowRebuildConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<PipelineRun[]>([]);

  function yesterday(): string {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  }

  function addHistory(action: string, status: PipelineRun['status'], message?: string) {
    const run: PipelineRun = {
      id: Date.now().toString(),
      timestamp: new Date().toLocaleTimeString(),
      action,
      status,
      message,
    };
    setHistory((prev) => [run, ...prev.slice(0, 19)]);
  }

  const runIndex = async (opts: { date?: string; start_date?: string; end_date?: string; force?: boolean }) => {
    setRunning(true);
    const label = opts.date
      ? `Index ${opts.date}`
      : opts.start_date
      ? `Index ${opts.start_date} to ${opts.end_date ?? 'today'}`
      : 'Rebuild';
    addHistory(label, 'running');
    try {
      const result = await triggerCatalogIndex({ ...opts, ...(crystallize ? {} : {}) });
      const ok = result?.status !== 'error';
      setHistory((prev) =>
        prev.map((r, i) =>
          i === 0 ? { ...r, status: ok ? 'success' : 'error', message: result?.message ?? undefined } : r,
        ),
      );
    } catch (e: any) {
      setHistory((prev) =>
        prev.map((r, i) => (i === 0 ? { ...r, status: 'error', message: String(e?.message ?? e) } : r)),
      );
    } finally {
      setRunning(false);
    }
  };

  const handleYesterday = () => runIndex({ date: yesterday() });

  const handleSpecificDate = () => {
    if (!specificDate) return;
    runIndex({ date: specificDate });
  };

  const handleDateRange = () => {
    if (!startDate) return;
    runIndex({ start_date: startDate, ...(endDate ? { end_date: endDate } : {}) });
  };

  const handleRebuild = () => {
    setShowRebuildConfirm(false);
    runIndex({ force: true });
  };

  const TIERS: { id: Tier; label: string; desc: string }[] = [
    { id: 'auto', label: 'Auto', desc: 'System decides' },
    { id: '1', label: 'Tier 1', desc: 'Archive only' },
    { id: '2', label: 'Tier 2', desc: 'Archive + catalog' },
    { id: '3', label: 'Tier 3', desc: 'Full pipeline' },
  ];

  return (
    <section className="flex flex-col gap-5 p-4 overflow-auto h-full" aria-label="Pipeline control">
      {/* Tier selector */}
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
          Processing Tier
        </h3>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Pipeline tier">
          {TIERS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={tier === t.id}
              onClick={() => setTier(t.id)}
              className={`
                flex flex-col items-start px-3 py-2 rounded-lg border text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent
                ${tier === t.id
                  ? 'border-accent bg-accent/15 text-shell-text'
                  : 'border-white/10 bg-white/[0.02] text-shell-text-secondary hover:bg-white/[0.04]'}
              `}
            >
              <span className="text-xs font-medium">{t.label}</span>
              <span className="text-[10px] text-shell-text-tertiary">{t.desc}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Crystallization toggle */}
      <div className="flex items-center gap-3">
        <Switch
          id="crystallize-toggle"
          checked={crystallize}
          onCheckedChange={setCrystallize}
          aria-label="Enable crystallization"
        />
        <Label htmlFor="crystallize-toggle" className="text-sm text-shell-text cursor-pointer">
          Enable crystallization
        </Label>
        <span className="text-xs text-shell-text-tertiary">(generate narratives)</span>
      </div>

      {/* Quick actions */}
      <div className="flex flex-col gap-3">
        <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
          Quick Actions
        </h3>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={handleYesterday}
            disabled={running}
            aria-label="Index yesterday's sessions"
            className="gap-1.5"
            size="sm"
          >
            <Play size={13} aria-hidden="true" />
            Index Yesterday
          </Button>
        </div>
      </div>

      {/* Specific date */}
      <Card className="bg-white/[0.02] border-white/8">
        <CardContent className="p-4 flex flex-col gap-3">
          <h3 className="text-xs font-medium text-shell-text">Specific Date</h3>
          <div className="flex items-center gap-2">
            <Input
              type="date"
              value={specificDate}
              onChange={(e) => setSpecificDate(e.target.value)}
              aria-label="Target date"
              className="h-8 text-xs flex-1 max-w-[160px]"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleSpecificDate}
              disabled={running || !specificDate}
              aria-label="Run index for selected date"
              className="gap-1.5 h-8"
            >
              <Play size={12} aria-hidden="true" />
              Run
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Date range */}
      <Card className="bg-white/[0.02] border-white/8">
        <CardContent className="p-4 flex flex-col gap-3">
          <h3 className="text-xs font-medium text-shell-text">Date Range</h3>
          <div className="flex items-center gap-2 flex-wrap">
            <Input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              aria-label="Start date"
              className="h-8 text-xs flex-1 min-w-[130px] max-w-[160px]"
            />
            <span className="text-xs text-shell-text-tertiary">to</span>
            <Input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              aria-label="End date (optional)"
              className="h-8 text-xs flex-1 min-w-[130px] max-w-[160px]"
              placeholder="today"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleDateRange}
              disabled={running || !startDate}
              aria-label="Run index for date range"
              className="gap-1.5 h-8"
            >
              <Play size={12} aria-hidden="true" />
              Run
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Rebuild */}
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
          Danger Zone
        </h3>
        {!showRebuildConfirm ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowRebuildConfirm(true)}
            disabled={running}
            aria-label="Rebuild entire memory index"
            className="self-start gap-1.5 border-red-500/30 text-red-400 hover:bg-red-500/10 hover:border-red-500/40"
          >
            <RefreshCw size={13} aria-hidden="true" />
            Rebuild Full Index
          </Button>
        ) : (
          <div className="flex items-center gap-2 p-3 rounded-lg border border-red-500/30 bg-red-500/10">
            <AlertTriangle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-300 flex-1">This will reindex everything. Continue?</p>
            <Button
              size="sm"
              onClick={handleRebuild}
              disabled={running}
              className="bg-red-600 hover:bg-red-700 text-white h-7 px-2.5 text-xs"
              aria-label="Confirm rebuild"
            >
              Rebuild
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowRebuildConfirm(false)}
              className="h-7 px-2.5 text-xs"
              aria-label="Cancel rebuild"
            >
              Cancel
            </Button>
          </div>
        )}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
            Pipeline History
          </h3>
          <div className="space-y-1.5" role="list" aria-label="Pipeline run history">
            {history.map((run) => (
              <div
                key={run.id}
                role="listitem"
                className="flex items-start gap-2.5 px-3 py-2 rounded-md border border-white/8 bg-white/[0.02]"
              >
                {run.status === 'running' && (
                  <RefreshCw size={13} className="text-blue-400 animate-spin mt-0.5 shrink-0" aria-hidden="true" />
                )}
                {run.status === 'success' && (
                  <CheckCircle size={13} className="text-green-400 mt-0.5 shrink-0" aria-hidden="true" />
                )}
                {run.status === 'error' && (
                  <AlertTriangle size={13} className="text-red-400 mt-0.5 shrink-0" aria-hidden="true" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-shell-text font-medium truncate">{run.action}</p>
                  {run.message && (
                    <p className="text-[11px] text-shell-text-tertiary mt-0.5 line-clamp-2">{run.message}</p>
                  )}
                </div>
                <span className="text-[10px] text-shell-text-tertiary flex items-center gap-1 shrink-0">
                  <Clock size={9} aria-hidden="true" />
                  {run.timestamp}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
