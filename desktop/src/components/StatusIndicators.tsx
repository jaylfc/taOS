import { useState, useEffect } from "react";
import { Cpu, MemoryStick, Zap, CircuitBoard } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";

interface SystemData {
  ram_mb?: number | null;
  ram_pct?: number | null;
  cpu_pct?: number | null;
  vram_mb?: number | null;
  vram_total_mb?: number | null;
  vram_pct?: number | null;
  npu_pct?: number | null;
  has_gpu?: boolean;
  has_npu?: boolean;
}

function Indicator({
  icon,
  label,
  value,
  colour,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | null;
  colour: string;
}) {
  return (
    <div
      className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-white/5 transition-colors"
      title={`${label}: ${value !== null ? `${Math.round(value)}%` : "\u2014"}`}
      aria-label={`${label} usage ${value !== null ? `${Math.round(value)} percent` : "unknown"}`}
    >
      <span className="text-shell-text-tertiary">{icon}</span>
      <div className="relative w-6 h-1 rounded-full bg-white/10 overflow-hidden">
        {value !== null && (
          <div
            className="absolute inset-y-0 left-0 transition-all rounded-full"
            style={{
              width: `${Math.min(100, Math.max(0, value))}%`,
              backgroundColor: colour,
            }}
          />
        )}
      </div>
    </div>
  );
}

function colourForLoad(pct: number | null): string {
  if (pct === null) return "rgba(255,255,255,0.2)";
  if (pct < 50) return "#43e97b";
  if (pct < 80) return "#febc2e";
  return "#ff5f57";
}

export function StatusIndicators({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<SystemData | null>(null);
  const openWindow = useProcessStore((s) => s.openWindow);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const res = await fetch("/api/system", {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return;
        const ct = res.headers.get("content-type") ?? "";
        if (!ct.includes("application/json")) return;
        const json = await res.json();
        if (cancelled) return;

        const resources = json.resources ?? json;
        const hw = json.hardware ?? json;

        const gpuType = hw?.gpu?.type;
        const npuType = hw?.npu?.type;

        setData({
          cpu_pct: resources.cpu_percent ?? resources.cpu_pct ?? null,
          ram_pct: resources.ram_percent ?? resources.ram_pct ?? null,
          ram_mb: resources.ram_used_mb ?? null,
          vram_pct: resources.vram_percent ?? resources.vram_pct ?? null,
          vram_mb: resources.vram_used_mb ?? null,
          vram_total_mb: hw?.gpu?.vram_mb ?? null,
          npu_pct: resources.npu_percent ?? resources.npu_pct ?? null,
          has_gpu: Boolean(gpuType && gpuType !== "none"),
          has_npu: Boolean(npuType && npuType !== "none"),
        });
      } catch {
        /* ignore */
      }
    };

    load();
    const interval = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const openDashboard = () => {
    const app = getApp("dashboard");
    if (app) openWindow("dashboard", app.defaultSize);
  };

  if (!data) {
    return null;
  }

  return (
    <button
      onClick={openDashboard}
      className={`flex items-center gap-0.5 ${compact ? "" : "px-1"}`}
      aria-label="Open Dashboard"
    >
      <Indicator
        icon={<Cpu size={11} />}
        label="CPU"
        value={data.cpu_pct ?? null}
        colour={colourForLoad(data.cpu_pct ?? null)}
      />
      <Indicator
        icon={<MemoryStick size={11} />}
        label="RAM"
        value={data.ram_pct ?? null}
        colour={colourForLoad(data.ram_pct ?? null)}
      />
      {data.has_gpu && (
        <Indicator
          icon={<CircuitBoard size={11} />}
          label="VRAM"
          value={data.vram_pct ?? null}
          colour={colourForLoad(data.vram_pct ?? null)}
        />
      )}
      {data.has_npu && (
        <Indicator
          icon={<Zap size={11} />}
          label="NPU"
          value={data.npu_pct ?? null}
          colour={colourForLoad(data.npu_pct ?? null)}
        />
      )}
    </button>
  );
}
