import { useState, useEffect } from "react";
import { Activity, Database, Archive, BookOpen, Sparkles, RefreshCw, AlertCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { fetchMemoryStats, fetchCatalogStats } from "@/lib/memory";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}

/* ------------------------------------------------------------------ */
/*  StatCard                                                           */
/* ------------------------------------------------------------------ */

function StatCard({ icon, label, value, sub, accent = "text-accent" }: StatCardProps) {
  return (
    <Card className="bg-white/[0.03] border-white/8">
      <CardContent className="p-4 flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${accent}`} aria-hidden="true">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-shell-text-tertiary uppercase tracking-wider mb-1">{label}</p>
          <p className="text-xl font-semibold text-shell-text tabular-nums">{value}</p>
          {sub && <p className="text-xs text-shell-text-tertiary mt-0.5">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  PipelineStatusBar                                                  */
/* ------------------------------------------------------------------ */

interface PipelineStatusBarProps {
  status: Record<string, any>;
}

function PipelineStatusBar({ status }: PipelineStatusBarProps) {
  const stages = [
    { key: 'archive', label: 'Archive' },
    { key: 'catalog', label: 'Catalog' },
    { key: 'vector', label: 'Vector' },
    { key: 'kg', label: 'KG' },
    { key: 'crystal', label: 'Crystal' },
  ];

  return (
    <div className="flex items-center gap-2 flex-wrap" role="list" aria-label="Pipeline stages">
      {stages.map((stage, i) => {
        const stageStatus = status[stage.key] ?? 'idle';
        const isActive = stageStatus === 'running';
        const isError = stageStatus === 'error';
        const isDone = stageStatus === 'done' || stageStatus === 'ok';

        return (
          <div key={stage.key} className="flex items-center gap-1.5" role="listitem">
            {i > 0 && <span className="text-white/20 text-xs" aria-hidden="true">→</span>}
            <span
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
                isActive
                  ? 'bg-blue-500/15 border-blue-500/30 text-blue-400'
                  : isError
                  ? 'bg-red-500/15 border-red-500/30 text-red-400'
                  : isDone
                  ? 'bg-green-500/15 border-green-500/30 text-green-400'
                  : 'bg-white/[0.04] border-white/8 text-shell-text-tertiary'
              }`}
              aria-label={`${stage.label}: ${stageStatus}`}
            >
              {isActive && (
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" aria-hidden="true" />
              )}
              {isError && <AlertCircle size={11} aria-hidden="true" />}
              {stage.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Dashboard                                                          */
/* ------------------------------------------------------------------ */

export function Dashboard() {
  const [stats, setStats] = useState<Record<string, any>>({});
  const [catalogStats, setCatalogStats] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const [s, cs] = await Promise.all([fetchMemoryStats(), fetchCatalogStats()]);
    setStats(s);
    setCatalogStats(cs);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const fmt = (v: any) => {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') return v.toLocaleString();
    return String(v);
  };

  return (
    <section className="flex flex-col gap-5 p-4 overflow-auto h-full" aria-label="Memory dashboard">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-shell-text">Overview</h2>
        <Button
          variant="ghost"
          size="sm"
          onClick={load}
          disabled={loading}
          aria-label="Refresh stats"
          className="h-7 px-2 gap-1.5 text-xs"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          Refresh
        </Button>
      </div>

      {/* Store stats */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <StatCard
          icon={<Database size={18} />}
          label="KG Entities"
          value={fmt(stats.kg_entities)}
          sub={`${fmt(stats.kg_triples)} triples`}
          accent="text-cyan-400"
        />
        <StatCard
          icon={<Activity size={18} />}
          label="Vector Chunks"
          value={fmt(stats.vector_count)}
          sub={stats.vector_backend ?? undefined}
          accent="text-blue-400"
        />
        <StatCard
          icon={<Archive size={18} />}
          label="Archive Events"
          value={fmt(stats.archive_events)}
          sub={stats.archive_size_mb ? `${stats.archive_size_mb} MB` : undefined}
          accent="text-amber-400"
        />
        <StatCard
          icon={<BookOpen size={18} />}
          label="Catalog Sessions"
          value={fmt(catalogStats.total_sessions ?? stats.catalog_sessions)}
          sub={catalogStats.date_range ?? undefined}
          accent="text-green-400"
        />
        <StatCard
          icon={<Sparkles size={18} />}
          label="Crystals"
          value={fmt(stats.crystals ?? catalogStats.crystals)}
          sub="narratives"
          accent="text-pink-400"
        />
        {stats.last_indexed && (
          <StatCard
            icon={<RefreshCw size={18} />}
            label="Last Indexed"
            value={String(stats.last_indexed).slice(0, 10)}
            sub={String(stats.last_indexed).slice(11, 19) || undefined}
            accent="text-shell-text-tertiary"
          />
        )}
      </div>

      {/* Pipeline status */}
      {stats.pipeline && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
            Pipeline Status
          </h3>
          <PipelineStatusBar status={stats.pipeline} />
        </div>
      )}

      {/* Recent errors */}
      {Array.isArray(stats.recent_errors) && stats.recent_errors.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-red-400 uppercase tracking-wider">Recent Errors</h3>
          <div className="space-y-1.5">
            {stats.recent_errors.slice(0, 5).map((err: string, i: number) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/20">
                <AlertCircle size={13} className="text-red-400 mt-0.5 shrink-0" aria-hidden="true" />
                <p className="text-xs text-red-300 break-words min-w-0">{err}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
