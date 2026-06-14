import { useState, useEffect, useMemo, useCallback } from "react";
import {
  X,
  Download,
  Check,
  HardDrive,
  Cpu,
  Zap,
  AlertTriangle,
  Loader2,
  Search,
  Package,
  Activity,
  CircuitBoard,
  Cloud,
} from "lucide-react";
import { Button, Card, CardContent, Input } from "@/components/ui";
import { CLOUD_PROVIDER_TYPES } from "@/lib/models";

interface ModelVariant {
  id: string;
  name: string;
  format?: string;
  size_mb?: number;
  min_ram_mb?: number;
  backend?: string[];
  downloaded?: boolean;
  compatibility: "green" | "yellow" | "red";
  download_url?: string;
  requires_npu?: string[];
  quality?: string;
}

interface LoadedModel {
  name: string;
  backend: string;
  backend_type: string;
  backend_url: string;
  purpose: string;
  size_mb: number | null;
  vram_mb: number | null;
  ram_mb: number | null;
  expires_at?: string | null;
  details?: Record<string, unknown>;
}

interface CatalogModel {
  id: string;
  name: string;
  description?: string;
  version?: string;
  license?: string;
  capabilities?: string[];
  variants?: ModelVariant[];
  has_downloaded_variant?: boolean;
}

interface CloudModel {
  id: string;
  name?: string;
  providerName: string;
  providerType: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  capability: string;
  onModelDownloaded?: (modelId: string, variantId: string) => void;
}

const COMPAT_LABELS: Record<string, string> = {
  green: "Compatible",
  yellow: "Partial",
  red: "Unsupported",
};

const COMPAT_COLOURS: Record<string, string> = {
  green: "bg-emerald-400",
  yellow: "bg-amber-400",
  red: "bg-red-400",
};

function formatSize(mb?: number): string {
  if (!mb) return "—";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

const CLOUD_BACKEND_TYPES: readonly string[] = CLOUD_PROVIDER_TYPES;

export function ModelBrowser({
  open,
  onClose,
  capability,
  onModelDownloaded,
}: Props) {
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [cloudModels, setCloudModels] = useState<CloudModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "compatible" | "downloaded" | "cloud">(
    "compatible",
  );
  const [downloading, setDownloading] = useState<
    Record<string, { percent: number; status: string }>
  >({});
  const [loadedModels, setLoadedModels] = useState<LoadedModel[]>([]);

  const refreshLoaded = useCallback(async () => {
    try {
      const res = await fetch("/api/models/loaded", {
        headers: { Accept: "application/json" },
      });
      const ct = res.headers.get("content-type") ?? "";
      if (res.ok && ct.includes("application/json")) {
        const data = await res.json();
        setLoadedModels(data.loaded ?? []);
      }
    } catch {
      setLoadedModels([]);
    }
  }, []);

  useEffect(() => {
    if (open) {
      refreshLoaded();
      const interval = setInterval(refreshLoaded, 5000);
      return () => clearInterval(interval);
    }
  }, [open, refreshLoaded]);

  const refreshCloud = useCallback(async () => {
    try {
      const res = await fetch("/api/providers", {
        headers: { Accept: "application/json" },
      });
      const ct = res.headers.get("content-type") ?? "";
      if (!res.ok || !ct.includes("application/json")) {
        setCloudModels([]);
        return;
      }
      const providers = await res.json();
      const entries: CloudModel[] = [];
      for (const p of (Array.isArray(providers) ? providers : [])) {
        if (!CLOUD_BACKEND_TYPES.includes(p.type)) continue;
        const pModels: { id?: string; name?: string }[] = Array.isArray(p.models) ? p.models : [];
        if (pModels.length === 0) {
          // Provider is configured but returned no model list — show placeholder entry
          entries.push({
            id: p.model ?? "default",
            providerName: p.name,
            providerType: p.type,
          });
        } else {
          for (const m of pModels) {
            const modelId = m.id ?? m.name ?? "unknown";
            entries.push({ id: modelId, name: m.name, providerName: p.name, providerType: p.type });
          }
        }
      }
      setCloudModels(entries);
    } catch {
      setCloudModels([]);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/models", {
        headers: { Accept: "application/json" },
      });
      const ct = res.headers.get("content-type") ?? "";
      if (!res.ok || !ct.includes("application/json")) {
        setModels([]);
      } else {
        const data = await res.json();
        const list = (data.models ?? []).filter((m: CatalogModel) =>
          m.capabilities?.includes(capability),
        );
        setModels(list);
      }
    } catch {
      setModels([]);
    }
    setLoading(false);
  }, [capability]);

  useEffect(() => {
    if (open) {
      setLoading(true);
      refresh();
      refreshCloud();
    }
  }, [open, refresh, refreshCloud]);

  const filteredLocal = useMemo(() => {
    if (filter === "cloud") return [];
    return models.filter((m) => {
      if (
        search &&
        !m.name.toLowerCase().includes(search.toLowerCase()) &&
        !m.description?.toLowerCase().includes(search.toLowerCase())
      ) {
        return false;
      }
      const variants = m.variants ?? [];
      if (filter === "downloaded" && !variants.some((v) => v.downloaded))
        return false;
      if (
        filter === "compatible" &&
        !variants.some((v) => v.compatibility !== "red")
      )
        return false;
      return true;
    });
  }, [models, search, filter]);

  const filteredCloud = useMemo(() => {
    if (filter === "downloaded" || filter === "compatible") return [];
    return cloudModels.filter((m) => {
      if (!search) return true;
      const q = search.toLowerCase();
      return m.id.toLowerCase().includes(q) || m.providerName.toLowerCase().includes(q) || m.providerType.toLowerCase().includes(q);
    });
  }, [cloudModels, search, filter]);

  // Keep a single alias for backward compat in render
  const filtered = filteredLocal;

  const startDownload = useCallback(
    async (modelId: string, variantId: string) => {
      const key = `${modelId}:${variantId}`;
      setDownloading((prev) => ({
        ...prev,
        [key]: { percent: 0, status: "starting" },
      }));
      try {
        const res = await fetch("/api/models/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: modelId, variant_id: variantId }),
        });
        const data = await res.json();
        if (!data.download_id) {
          setDownloading((prev) => {
            const n = { ...prev };
            delete n[key];
            return n;
          });
          return;
        }
        const interval = setInterval(async () => {
          try {
            const pr = await fetch(`/api/models/downloads/${data.download_id}`);
            const task = await pr.json();
            setDownloading((prev) => ({
              ...prev,
              [key]: {
                percent: task.percent ?? 0,
                status: task.status ?? "downloading",
              },
            }));
            if (task.status === "complete") {
              clearInterval(interval);
              setDownloading((prev) => {
                const n = { ...prev };
                delete n[key];
                return n;
              });
              await refresh();
              onModelDownloaded?.(modelId, variantId);
            } else if (task.status === "error") {
              clearInterval(interval);
              setDownloading((prev) => {
                const n = { ...prev };
                delete n[key];
                return n;
              });
            }
          } catch {
            clearInterval(interval);
          }
        }, 2000);
      } catch {
        setDownloading((prev) => {
          const n = { ...prev };
          delete n[key];
          return n;
        });
      }
    },
    [refresh, onModelDownloaded],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[10003] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      style={{
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 16px)",
        paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35 + 16px)",
        paddingLeft: "16px",
        paddingRight: "16px",
      }}
    >
      <div
        className="w-full max-w-4xl h-full max-h-full flex flex-col rounded-2xl border border-white/10 overflow-hidden"
        style={{ backgroundColor: "var(--color-dock-bg)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <div className="flex items-center gap-2">
            <Package size={18} className="text-accent" />
            <h2 className="text-base font-semibold text-shell-text">
              Model Browser
            </h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </Button>
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-white/10">
          <div className="flex-1 min-w-[200px] relative">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none"
            />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models..."
              className="pl-9"
            />
          </div>
          <div className="flex gap-1 flex-wrap">
            {(["all", "compatible", "downloaded", "cloud"] as const).map((f) => (
              <Button
                key={f}
                variant={filter === f ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setFilter(f)}
              >
                {f === "all"
                  ? "All"
                  : f === "compatible"
                    ? "Local"
                    : f === "downloaded"
                      ? "Downloaded"
                      : "Cloud"}
              </Button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Loaded Models */}
          {loadedModels.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <Activity size={14} className="text-emerald-400" />
                <h3 className="text-xs font-semibold uppercase tracking-wider text-shell-text-secondary">
                  Loaded Models ({loadedModels.length})
                </h3>
                <span className="text-[10px] text-shell-text-tertiary">
                  Currently running in memory
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {loadedModels.map((m, i) => (
                  <Card key={`${m.backend}-${m.name}-${i}`} className="p-3">
                    <CardContent className="p-0">
                      <div className="flex items-start justify-between gap-2 mb-1.5">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                            <span className="text-xs font-medium text-shell-text truncate">
                              {m.name}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 mt-1 text-[10px] text-shell-text-tertiary">
                            <span className="px-1.5 py-0.5 rounded-full bg-white/5">
                              {m.purpose}
                            </span>
                            <span>via {m.backend}</span>
                          </div>
                        </div>
                      </div>
                      {(m.size_mb !== null || m.vram_mb !== null) && (
                        <div className="flex items-center gap-3 pt-1.5 border-t border-white/5 text-[10px] text-shell-text-secondary">
                          {m.size_mb !== null && m.size_mb > 0 && (
                            <span className="flex items-center gap-1">
                              <HardDrive size={9} />
                              {formatSize(m.size_mb)}
                            </span>
                          )}
                          {m.vram_mb !== null && m.vram_mb > 0 && (
                            <span className="flex items-center gap-1 text-blue-400">
                              <CircuitBoard size={9} />
                              VRAM {formatSize(m.vram_mb)}
                            </span>
                          )}
                          {m.ram_mb !== null && m.ram_mb > 0 && (
                            <span className="flex items-center gap-1">
                              <Cpu size={9} />
                              RAM {formatSize(m.ram_mb)}
                            </span>
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
              <div className="border-t border-white/5 mt-4" />
            </div>
          )}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2
                size={24}
                className="animate-spin text-shell-text-tertiary"
              />
            </div>
          ) : filtered.length === 0 && filteredCloud.length === 0 ? (
            <div className="text-center py-12 text-shell-text-tertiary">
              <Package size={32} className="mx-auto mb-3" />
              <p className="text-sm">No models match your criteria</p>
            </div>
          ) : (
            <>
              {/* Local catalog models */}
              {filtered.map((model) => (
                <Card key={model.id} className="p-4">
                  <CardContent className="p-0">
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-sm font-semibold text-shell-text">
                          {model.name}
                        </h3>
                        {model.description && (
                          <p className="text-xs text-shell-text-secondary mt-1 line-clamp-2">
                            {model.description}
                          </p>
                        )}
                        {model.license && (
                          <span className="inline-block mt-1.5 text-[10px] px-2 py-0.5 rounded-full bg-white/5 text-shell-text-tertiary">
                            {model.license}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="space-y-1.5 mt-3">
                      {(model.variants ?? []).map((variant) => {
                        const key = `${model.id}:${variant.id}`;
                        const dl = downloading[key];
                        return (
                          <div
                            key={variant.id}
                            className="flex items-center gap-3 p-2.5 rounded-lg bg-white/[0.02] border border-white/[0.04]"
                          >
                            <div
                              className={`w-2 h-2 rounded-full ${COMPAT_COLOURS[variant.compatibility] ?? "bg-white/20"} shrink-0`}
                              title={
                                COMPAT_LABELS[variant.compatibility] ?? "Unknown"
                              }
                            />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium text-shell-text truncate">
                                  {variant.name}
                                </span>
                                {variant.downloaded && (
                                  <Check
                                    size={12}
                                    className="text-emerald-400 shrink-0"
                                  />
                                )}
                              </div>
                              <div className="flex items-center gap-3 mt-0.5 text-[10px] text-shell-text-tertiary">
                                <span className="flex items-center gap-1">
                                  <HardDrive size={10} />
                                  {formatSize(variant.size_mb)}
                                </span>
                                {variant.min_ram_mb && (
                                  <span className="flex items-center gap-1">
                                    <Cpu size={10} />
                                    {formatSize(variant.min_ram_mb)} RAM
                                  </span>
                                )}
                                {variant.requires_npu &&
                                  variant.requires_npu.length > 0 && (
                                    <span className="flex items-center gap-1 text-amber-400">
                                      <Zap size={10} />
                                      NPU: {variant.requires_npu.join(", ")}
                                    </span>
                                  )}
                                {variant.format && (
                                  <span className="text-shell-text-tertiary">
                                    {variant.format.toUpperCase()}
                                  </span>
                                )}
                              </div>
                            </div>
                            <div className="shrink-0">
                              {dl ? (
                                <div className="flex items-center gap-2 text-xs text-shell-text-secondary">
                                  <Loader2
                                    size={12}
                                    className="animate-spin"
                                  />
                                  {dl.percent.toFixed(0)}%
                                </div>
                              ) : variant.downloaded ? (
                                <span className="text-xs text-emerald-400 px-2">
                                  Installed
                                </span>
                              ) : variant.compatibility === "red" ? (
                                <span
                                  className="text-[10px] text-red-400 flex items-center gap-1 px-2"
                                  title="Not compatible with this hardware"
                                >
                                  <AlertTriangle size={10} />
                                  Unsupported
                                </span>
                              ) : (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    startDownload(model.id, variant.id)
                                  }
                                >
                                  <Download size={12} />
                                  Download
                                </Button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              ))}

              {/* Cloud provider models */}
              {filteredCloud.length > 0 && (
                <>
                  {filtered.length > 0 && (
                    <div className="flex items-center gap-2 pt-2">
                      <div className="flex-1 border-t border-white/5" />
                      <span className="text-[10px] text-shell-text-tertiary uppercase tracking-wide px-2">Cloud</span>
                      <div className="flex-1 border-t border-white/5" />
                    </div>
                  )}
                  {filteredCloud.map((cm) => (
                    <Card key={`cloud-${cm.providerName}-${cm.id}`} className="p-4">
                      <CardContent className="p-0">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <Cloud size={13} className="text-cyan-400 shrink-0" />
                              <h3 className="text-sm font-semibold text-shell-text truncate">
                                {cm.name ?? cm.id}
                              </h3>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-medium shrink-0">
                                {cm.providerName}
                              </span>
                            </div>
                            <p className="text-xs text-shell-text-secondary mt-1">{cm.providerType}</p>
                            {cm.name && cm.name !== cm.id && (
                              <p className="text-[10px] text-shell-text-tertiary mt-0.5 font-mono">{cm.id}</p>
                            )}
                          </div>
                          <Button
                            variant="outline"
                            size="sm"
                            className="shrink-0"
                            onClick={() => onModelDownloaded?.(cm.id, "cloud")}
                            aria-label={`Use ${cm.id} from ${cm.providerName} in agent`}
                          >
                            Use in agent
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
