import { useState, useMemo, useRef } from "react";
import { Search, X } from "lucide-react";
import { getAllApps, getApp, getOrRegisterServiceApp } from "@/registry/app-registry";
import { useProcessStore } from "@/stores/process-store";
import { useShortcut } from "@/hooks/use-shortcut-registry";
import { LaunchpadIcon } from "./LaunchpadIcon";
import { ServiceIcon } from "./ServiceIcon";
import { useInstalledServices } from "@/hooks/use-installed-services";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenApp?: (windowId: string) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  platform: "Platform",
  os: "Utilities",
  streaming: "Streaming Apps",
  game: "Games",
};

export function Launchpad({ open, onClose, onOpenApp }: Props) {
  const [query, setQuery] = useState("");
  const openRef = useRef(open);
  openRef.current = open;
  const { openWindow } = useProcessStore();
  const installedServices = useInstalledServices();

  // Register Escape at overlay priority so it beats any system shortcuts when open
  useShortcut("Escape", () => { if (openRef.current) onClose(); }, "Close launchpad", "overlay");
  // Detect mobile to skip autoFocus (prevents iOS keyboard popping automatically)
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  const apps = useMemo(() => {
    const all = getAllApps();
    if (!query.trim()) return all;
    const q = query.toLowerCase();
    return all.filter((a) => a.name.toLowerCase().includes(q));
  }, [query]);

  const grouped = useMemo(() => {
    const groups: Record<string, typeof apps> = {};
    for (const app of apps) {
      (groups[app.category] ??= []).push(app);
    }
    return groups;
  }, [apps]);

  const handleLaunch = (appId: string) => {
    const app = getApp(appId);
    if (app) {
      const wid = openWindow(appId, app.defaultSize);
      onOpenApp?.(wid);
    }
    onClose();
    setQuery("");
  };

  const handleLaunchService = (appId: string, displayName: string, url: string) => {
    const manifest = getOrRegisterServiceApp(appId, displayName);
    const wid = openWindow(manifest.id, manifest.defaultSize, { url, displayName });
    onOpenApp?.(wid);
    onClose();
    setQuery("");
  };

  // Filter services by search query if one is active
  const filteredServices = useMemo(() => {
    if (!query.trim()) return installedServices;
    const q = query.toLowerCase();
    return installedServices.filter((s) => s.display_name.toLowerCase().includes(q));
  }, [installedServices, query]);

  if (!open) return null;

  return (
    <div
      className="fixed top-0 left-0 right-0 z-[9000] flex flex-col backdrop-blur-md bg-black/40"
      onClick={onClose}
      style={{
        bottom: isMobile ? 76 : 0,
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 60px)",
        paddingBottom: isMobile ? 16 : "calc(52px + env(safe-area-inset-bottom, 0px) + 16px)",
      }}
    >
      <div
        className="w-full max-w-3xl mx-auto px-4 flex-1 flex flex-col min-h-0"
      >
        <div
          className="flex items-center gap-2 px-4 py-2 mb-4 rounded-xl bg-white/10 border border-white/10 shrink-0"
          onClick={(e) => e.stopPropagation()}
        >
          <Search size={16} className="text-shell-text-tertiary" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search apps..."
            className="flex-1 bg-transparent text-sm text-shell-text outline-none placeholder:text-shell-text-tertiary"
            autoFocus={!isMobile}
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search">
              <X size={14} className="text-shell-text-tertiary" />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto space-y-6 pr-1">
          {Object.entries(grouped).map(([category, categoryApps]) => (
            <div key={category}>
              <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wide mb-3 px-1">
                {CATEGORY_LABELS[category] ?? category}
              </h3>
              <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 gap-3">
                {categoryApps.map((app) => (
                  <LaunchpadIcon key={app.id} app={app} onClick={() => handleLaunch(app.id)} />
                ))}
              </div>
            </div>
          ))}

          {filteredServices.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wide mb-3 px-1">
                Apps
              </h3>
              <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 gap-3">
                {filteredServices.map((svc) => (
                  <ServiceIcon
                    key={svc.app_id}
                    service={svc}
                    onClick={() => handleLaunchService(svc.app_id, svc.display_name, svc.url)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
