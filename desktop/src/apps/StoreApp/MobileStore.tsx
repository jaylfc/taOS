import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import {
  Search, Check, Package, Loader2, Star, Compass,
  Grid2x2, Bot, RefreshCw, ChevronRight, Cpu, X, ArrowDownToLine,
} from "lucide-react";
import type { CatalogApp, InstallTarget } from "./types";
import type { ResolveResponse } from "./resolver-types";
import { filterCatalog, compatFromResolver } from "./filter";
import { AppIcon, StoreCover } from "./AppIcon";

/* ------------------------------------------------------------------
   Mobile Store - Apple App Store-style presentation.

   Switched on from index.tsx via useIsMobile(). The desktop render
   path is untouched. All catalog data, install logic, device/backend
   filtering and resolver state are owned by StoreApp and passed in;
   this file only restructures the PRESENTATION for a phone.

   Icons resolve through the shared AppIcon component (logo with a
   branded-monogram fallback), so no app tile ever shows up blank.
   ------------------------------------------------------------------ */

function formatStars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/* The row subtitle: a tagline reads in its own sentence case; the bare
   category fallback (e.g. "agent-framework") gets de-slugged and capitalised. */
function subtitleFor(app: CatalogApp): string {
  if (app.tagline) return app.tagline;
  const raw = (app.category || app.type).replace(/-/g, " ");
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/* ------------------------------------------------------------------
   GetButton - the App Store pill. "Get" / spinner / "Open"
   ------------------------------------------------------------------ */

function GetButton({
  app, onInstall, installTargets,
}: {
  app: CatalogApp;
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const handleGet = useCallback(async () => {
    if (app.installed || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const target = installTargets[0]?.name ?? "local";
      const res = await fetch("/api/store/install-v2", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ app_id: app.id, target_remote: target }),
      });
      if (res.ok) onInstall(app.id);
      else setFailed(true);
    } catch { setFailed(true); }
    setBusy(false);
  }, [app.id, app.installed, busy, installTargets, onInstall]);

  if (app.installed) {
    // Installed services are managed elsewhere, not launched from the store,
    // so this is an honest status indicator rather than a fake "Open" action.
    return (
      <span
        role="status"
        aria-label={`${app.name} installed`}
        className="shrink-0 inline-flex items-center gap-1 text-[12px] font-semibold text-emerald-400 px-2"
      >
        <Check className="w-3.5 h-3.5" /> Installed
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={handleGet}
      disabled={busy}
      aria-label={failed ? `Retry installing ${app.name}` : `Get ${app.name}`}
      className={`shrink-0 min-w-[68px] h-9 inline-flex items-center justify-center rounded-full text-[13px] font-bold tracking-wide active:scale-[0.94] transition-transform ${
        failed
          ? "bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/40"
          : "bg-shell-surface-active text-shell-text"
      }`}
    >
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : failed ? "Retry" : "Get"}
    </button>
  );
}

/* ------------------------------------------------------------------
   AppRow - App Store list row: icon + title + subtitle + Get pill
   ------------------------------------------------------------------ */

function AppRow({
  app, onInstall, installTargets,
}: {
  app: CatalogApp;
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <AppIcon app={app} size={56} />
      <div className="flex-1 min-w-0">
        <div className="text-[15px] font-semibold text-shell-text leading-tight truncate">{app.name}</div>
        <div className="text-[12.5px] text-shell-text-secondary leading-tight truncate">{subtitleFor(app)}</div>
        {app.stars ? (
          <div className="mt-0.5 flex items-center gap-1 text-[11px] text-shell-text-tertiary">
            <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
            {formatStars(app.stars)}
          </div>
        ) : null}
      </div>
      <GetButton app={app} onInstall={onInstall} installTargets={installTargets} />
    </div>
  );
}

/* A vertical list of AppRows with hairline dividers between them. */
function AppRowList({
  apps, onInstall, installTargets,
}: {
  apps: CatalogApp[];
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  return (
    <div className="flex flex-col divide-y divide-shell-border">
      {apps.map((app) => (
        <AppRow key={app.id} app={app} onInstall={onInstall} installTargets={installTargets} />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------
   FeatureCard - large featured hero (Editor's Choice) and the
   snap-scroll carousel cards. Full-bleed cover with overlaid meta.
   ------------------------------------------------------------------ */

function FeatureCard({
  app, onInstall, installTargets, hero,
}: {
  app: CatalogApp;
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
  hero?: boolean;
}) {
  return (
    <article
      className={`relative shrink-0 overflow-hidden rounded-3xl border border-shell-border-strong ${hero ? "w-full" : "w-[84%] max-w-[330px]"}`}
      style={{ scrollSnapAlign: "start" }}
    >
      {/* Cover: real photo with gradient fallback + legibility scrim */}
      <div className={`relative ${hero ? "h-52" : "h-44"}`}>
        <StoreCover app={app} />
      </div>
      {hero && (
        <div className="absolute top-3.5 left-4 text-[11px] font-bold uppercase tracking-[0.14em] text-white" style={{ textShadow: "0 1px 3px rgba(0,0,0,0.55)" }}>
          Editor's Choice
        </div>
      )}
      {/* Footer meta - sits over the cover scrim, so use white-on-dark */}
      <div className="absolute inset-x-0 bottom-0 p-3.5 flex items-center gap-3">
        <AppIcon app={app} size={hero ? 56 : 48} />
        <div className="flex-1 min-w-0" style={{ textShadow: "0 1px 3px rgba(0,0,0,0.55)" }}>
          <div className={`${hero ? "text-[17px]" : "text-[15px]"} font-bold text-white leading-tight truncate`}>{app.name}</div>
          <div className="text-[12.5px] text-white/80 leading-tight truncate">
            {subtitleFor(app)}
          </div>
        </div>
        <GetButton app={app} onInstall={onInstall} installTargets={installTargets} />
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------
   Carousel - horizontal snap-scroll strip with peek of the next card
   ------------------------------------------------------------------ */

function Carousel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="flex gap-3.5 overflow-x-auto px-4 pb-1"
      style={{ scrollSnapType: "x mandatory", scrollbarWidth: "none", WebkitOverflowScrolling: "touch" }}
    >
      {children}
      <div className="shrink-0 w-px" aria-hidden />
    </div>
  );
}

/* Section heading with optional "See all" affordance. */
function SectionHead({ title, sub, onSeeAll }: { title: string; sub?: string; onSeeAll?: () => void }) {
  return (
    <div className="flex items-end justify-between px-4 mb-2.5">
      <div className="min-w-0">
        {sub && <div className="text-[12px] text-shell-text-secondary leading-tight">{sub}</div>}
        <h2 className="text-[21px] font-bold text-shell-text tracking-[-0.01em] leading-tight truncate">{title}</h2>
      </div>
      {onSeeAll && (
        <button
          type="button"
          onClick={onSeeAll}
          className="shrink-0 text-[14px] font-medium active:opacity-60 transition-opacity"
          style={{ color: "var(--color-accent-strong)" }}
        >
          See all
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------
   Tab bar
   ------------------------------------------------------------------ */

type MobileTab = "discover" | "apps" | "agents" | "search" | "updates";

const TABS: { id: MobileTab; label: string; icon: React.ReactNode }[] = [
  { id: "discover", label: "Discover", icon: <Compass size={22} /> },
  { id: "apps",     label: "Apps",     icon: <Grid2x2 size={22} /> },
  { id: "agents",   label: "Agents",   icon: <Bot size={22} /> },
  { id: "search",   label: "Search",   icon: <Search size={22} /> },
  { id: "updates",  label: "Updates",  icon: <RefreshCw size={22} /> },
];

function TabBar({ active, onSelect }: { active: MobileTab; onSelect: (t: MobileTab) => void }) {
  return (
    <nav
      className="shrink-0 flex items-stretch border-t"
      style={{
        backgroundColor: "var(--color-dock-bg)",
        borderColor: "var(--color-dock-border)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
      aria-label="Store sections"
    >
      {TABS.map((t) => {
        const on = active === t.id;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onSelect(t.id)}
            aria-current={on ? "page" : undefined}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 pt-2 pb-1.5 active:opacity-60 transition-opacity"
            style={{ color: on ? "var(--color-accent-strong)" : "var(--color-shell-text-tertiary)" }}
          >
            {t.icon}
            <span className="text-[10px] font-medium tracking-tight">{t.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

/* ------------------------------------------------------------------
   MobileStore
   ------------------------------------------------------------------ */

const NAV_TYPE_MAP: Record<string, string[]> = {
  apps: ["streaming-app", "ai-app", "productivity", "home", "monitoring", "automation", "image-gen", "voice", "video-gen", "plugin"],
  agents: ["agent-framework"],
  updates: [],
};

interface Props {
  apps: CatalogApp[];
  loading: boolean;
  installTargets: InstallTarget[];
  selectedDevices: string[];
  onDevicesChange: (next: string[]) => void;
  selectedBackends: string[];
  compatMap: Map<string, ResolveResponse>;
  onInstall: (id: string) => void;
}

export function MobileStore({
  apps, loading, installTargets, selectedDevices, onDevicesChange,
  selectedBackends, compatMap, onInstall,
}: Props) {
  const [tab, setTab] = useState<MobileTab>("discover");
  const [search, setSearch] = useState("");
  const [deviceSheet, setDeviceSheet] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Reset scroll to top whenever the section changes.
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); }, [tab]);

  // Focus the search field when the Search tab opens.
  useEffect(() => {
    if (tab === "search") {
      const id = window.setTimeout(() => searchInputRef.current?.focus(), 60);
      return () => window.clearTimeout(id);
    }
  }, [tab]);

  /* Device-aware compatible list for the current tab, reusing the same
     helpers the desktop grid uses so device/backend filters and the model
     resolver still gate what shows. */
  const compatibleFor = useCallback((pool: CatalogApp[]): CatalogApp[] => {
    const selDevObjs = installTargets.filter((t) => selectedDevices.includes(t.name));
    const { compatible } = filterCatalog(pool, selDevObjs, selectedBackends);
    return compatible.filter((a) =>
      a.type !== "model" || compatFromResolver(a.id, compatMap, false),
    );
  }, [installTargets, selectedDevices, selectedBackends, compatMap]);

  const tabPool = useMemo(() => {
    const types = NAV_TYPE_MAP[tab] ?? [];
    if (tab === "updates") return apps.filter((a) => a.installed && a.update_available === true);
    if (types.length === 0) return apps;
    return apps.filter((a) => types.includes(a.type) || types.includes(a.category ?? ""));
  }, [apps, tab]);

  const tabApps = useMemo(() => compatibleFor(tabPool), [compatibleFor, tabPool]);

  const searchResults = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [];
    return compatibleFor(
      apps.filter((a) => a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q)),
    );
  }, [apps, search, compatibleFor]);

  /* Discover sections, mirroring the desktop curation. */
  const discoverApps = useMemo(() => compatibleFor(apps), [compatibleFor, apps]);
  const hero = useMemo(
    () => discoverApps.find((a) => a.id === "comfyui") ?? discoverApps.find((a) => a.cover) ?? discoverApps[0],
    [discoverApps],
  );
  const popular = useMemo(
    () => [...discoverApps].filter((a) => (a.stars ?? 0) > 0).sort((a, b) => (b.stars ?? 0) - (a.stars ?? 0)).slice(0, 8),
    [discoverApps],
  );
  const subscriptions = useMemo(() => {
    const ids = ["sonarr", "radarr", "qbittorrent", "sabnzbd", "homebridge", "adguard-home", "uptime-kuma", "nextcloud"];
    return ids.map((id) => discoverApps.find((a) => a.id === id)).filter(Boolean).slice(0, 6) as CatalogApp[];
  }, [discoverApps]);
  const frameworks = useMemo(
    () => discoverApps.filter((a) => a.type === "agent-framework" || a.category === "agent-framework").slice(0, 6),
    [discoverApps],
  );

  // Header title per section.
  const headerTitle =
    tab === "discover" ? "Discover" :
    tab === "apps" ? "Apps" :
    tab === "agents" ? "Agents" :
    tab === "search" ? "Search" : "Updates";

  const selectedDeviceLabel = useMemo(() => {
    if (selectedDevices.length === 0) return "All devices";
    if (selectedDevices.length === 1) {
      const d = installTargets.find((t) => t.name === selectedDevices[0]);
      return d?.friendly_name ?? d?.label ?? "1 device";
    }
    return `${selectedDevices.length} devices`;
  }, [selectedDevices, installTargets]);

  const goSearch = useCallback(() => setTab("search"), []);

  return (
    <div className="flex flex-col h-full bg-shell-bg" style={{ overscrollBehavior: "contain" }}>
      {/* Sticky header */}
      <header className="shrink-0 px-4 pt-3 pb-2.5 border-b border-shell-border bg-shell-bg/95" style={{ backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" }}>
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-[26px] font-extrabold text-shell-text tracking-[-0.02em] leading-none truncate">{headerTitle}</h1>
          <div className="flex items-center gap-2 shrink-0">
            {/* Device chip - folds the device pill bar into the header */}
            {installTargets.length > 1 && (
              <button
                type="button"
                onClick={() => setDeviceSheet(true)}
                className="inline-flex items-center gap-1.5 h-8 pl-2.5 pr-3 rounded-full border border-shell-border bg-shell-surface text-[12.5px] text-shell-text-secondary active:opacity-60 transition-opacity max-w-[150px]"
                aria-label={`Filter by device. Current: ${selectedDeviceLabel}`}
              >
                <Cpu className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--color-accent)" }} />
                <span className="truncate">{selectedDeviceLabel}</span>
              </button>
            )}
            {tab !== "search" && (
              <button
                type="button"
                onClick={goSearch}
                aria-label="Search the store"
                className="flex items-center justify-center w-8 h-8 rounded-full border border-shell-border bg-shell-surface active:opacity-60 transition-opacity"
              >
                <Search className="w-4 h-4 text-shell-text-secondary" />
              </button>
            )}
          </div>
        </div>
        {tab === "search" && (
          <div className="relative mt-2.5">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-shell-text-tertiary pointer-events-none" />
            <input
              ref={searchInputRef}
              type="text"
              inputMode="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Apps, agents, models, MCP servers"
              aria-label="Search the store"
              className="w-full h-10 pl-9 pr-9 rounded-xl bg-shell-surface border border-shell-border text-[15px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-shell-border-strong"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 w-6 h-6 flex items-center justify-center rounded-full bg-shell-surface-active active:opacity-60"
              >
                <X className="w-3.5 h-3.5 text-shell-text-secondary" />
              </button>
            )}
          </div>
        )}
      </header>

      {/* Scrolling feed */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto" style={{ WebkitOverflowScrolling: "touch" }}>
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-6 h-6 text-shell-text-tertiary animate-spin" />
          </div>
        ) : tab === "search" ? (
          <SearchView search={search} results={searchResults} onInstall={onInstall} installTargets={installTargets} />
        ) : tab === "discover" ? (
          <div className="flex flex-col gap-7 pt-4 pb-6">
            {hero && (
              <div className="px-4">
                <FeatureCard app={hero} onInstall={onInstall} installTargets={installTargets} hero />
              </div>
            )}
            {popular.length > 0 && (
              <section>
                <SectionHead sub="This week" title="Popular now" onSeeAll={() => setTab("apps")} />
                <Carousel>
                  {popular.map((app) => (
                    <FeatureCard key={app.id} app={app} onInstall={onInstall} installTargets={installTargets} />
                  ))}
                </Carousel>
              </section>
            )}
            {subscriptions.length > 0 && (
              <section className="px-4">
                <SectionHead sub="Self-hosted" title="Replace your subscriptions" />
                <AppRowList apps={subscriptions} onInstall={onInstall} installTargets={installTargets} />
              </section>
            )}
            {frameworks.length > 0 && (
              <section className="px-4">
                <SectionHead sub="Frameworks" title="Build agents with" onSeeAll={() => setTab("agents")} />
                <AppRowList apps={frameworks} onInstall={onInstall} installTargets={installTargets} />
              </section>
            )}
          </div>
        ) : (
          <SectionView
            title={headerTitle}
            apps={tabApps}
            onInstall={onInstall}
            installTargets={installTargets}
            empty={
              tab === "updates"
                ? { icon: <RefreshCw className="w-9 h-9" />, line: "You're all up to date" }
                : { icon: <Package className="w-9 h-9" />, line: "Nothing here yet" }
            }
            onBrowse={() => setTab("discover")}
          />
        )}
      </div>

      {/* Bottom tab bar */}
      <TabBar active={tab} onSelect={setTab} />

      {/* Device filter sheet */}
      {deviceSheet && (
        <DeviceSheet
          installTargets={installTargets}
          selected={selectedDevices}
          onChange={onDevicesChange}
          onClose={() => setDeviceSheet(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------
   SectionView - a plain App Store list for Apps / Agents / Updates
   ------------------------------------------------------------------ */

function SectionView({
  title, apps, onInstall, installTargets, empty, onBrowse,
}: {
  title: string;
  apps: CatalogApp[];
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
  empty: { icon: React.ReactNode; line: string };
  onBrowse: () => void;
}) {
  if (apps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 px-8 py-20 text-center">
        <span className="text-shell-text-tertiary">{empty.icon}</span>
        <p className="text-[15px] text-shell-text-secondary">{empty.line}</p>
        <button
          type="button"
          onClick={onBrowse}
          className="mt-1 inline-flex items-center gap-1 text-[14px] font-semibold active:opacity-60"
          style={{ color: "var(--color-accent-strong)" }}
        >
          Browse Discover <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    );
  }
  return (
    <div className="px-4 pt-3 pb-6">
      <div className="text-[12.5px] text-shell-text-tertiary mb-1">{apps.length} {apps.length === 1 ? "result" : "results"} in {title}</div>
      <AppRowList apps={apps} onInstall={onInstall} installTargets={installTargets} />
    </div>
  );
}

/* ------------------------------------------------------------------
   SearchView
   ------------------------------------------------------------------ */

function SearchView({
  search, results, onInstall, installTargets,
}: {
  search: string;
  results: CatalogApp[];
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  const q = search.trim();
  if (!q) {
    return (
      <div className="flex flex-col items-center justify-center gap-2.5 px-8 py-24 text-center">
        <Search className="w-9 h-9 text-shell-text-tertiary" />
        <p className="text-[15px] text-shell-text-secondary">Find apps, agents, models and more</p>
      </div>
    );
  }
  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2.5 px-8 py-24 text-center">
        <Package className="w-9 h-9 text-shell-text-tertiary" />
        <p className="text-[15px] text-shell-text-secondary">No results for &ldquo;{q}&rdquo;</p>
      </div>
    );
  }
  return (
    <div className="px-4 pt-3 pb-6">
      <div className="text-[12.5px] text-shell-text-tertiary mb-1">{results.length} {results.length === 1 ? "result" : "results"}</div>
      <AppRowList apps={results} onInstall={onInstall} installTargets={installTargets} />
    </div>
  );
}

/* ------------------------------------------------------------------
   DeviceSheet - bottom sheet replacing the desktop device pill bar
   ------------------------------------------------------------------ */

function DeviceSheet({
  installTargets, selected, onChange, onClose,
}: {
  installTargets: InstallTarget[];
  selected: string[];
  onChange: (next: string[]) => void;
  onClose: () => void;
}) {
  const selSet = new Set(selected);
  const toggle = (name: string) => {
    onChange(selSet.has(name) ? selected.filter((n) => n !== name) : [...selected, name]);
  };
  return (
    <div className="absolute inset-0 z-50 flex flex-col justify-end" role="dialog" aria-modal="true" aria-label="Filter by device">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/50" />
      <div
        className="relative rounded-t-3xl border-t border-shell-border-strong bg-shell-bg-deep px-4 pt-3 pb-5"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 20px)" }}
      >
        <div className="mx-auto mb-3 h-1 w-9 rounded-full bg-shell-surface-active" aria-hidden />
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[17px] font-bold text-shell-text">Filter by device</h3>
          {selected.length > 0 && (
            <button type="button" onClick={() => onChange([])} className="text-[13px] font-medium active:opacity-60" style={{ color: "var(--color-accent-strong)" }}>
              Clear
            </button>
          )}
        </div>
        <div className="flex flex-col gap-1">
          {installTargets.map((d) => {
            const on = selSet.has(d.name);
            return (
              <button
                key={d.name}
                type="button"
                onClick={() => toggle(d.name)}
                aria-pressed={on}
                className="flex items-center gap-3 px-3 py-3 rounded-xl active:bg-shell-surface transition-colors"
                style={{ background: on ? "var(--color-accent-soft)" : "transparent" }}
              >
                <Cpu className="w-4 h-4 shrink-0" style={{ color: on ? "var(--color-accent-strong)" : "var(--color-shell-text-tertiary)" }} />
                <span className="flex-1 text-left text-[15px] text-shell-text truncate">{d.friendly_name ?? d.label}</span>
                {d.tier_id && d.hardware_known !== false && (
                  <span className="text-[11px] uppercase tracking-wide text-shell-text-tertiary">{d.tier_id.replace(/^arm-|^x86-|^apple-/, "")}</span>
                )}
                {on && <Check className="w-4 h-4 shrink-0" style={{ color: "var(--color-accent-strong)" }} />}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full h-11 rounded-xl bg-shell-surface-active text-shell-text text-[15px] font-semibold active:scale-[0.98] transition-transform inline-flex items-center justify-center gap-2"
        >
          <ArrowDownToLine className="w-4 h-4" /> Done
        </button>
      </div>
    </div>
  );
}

export default MobileStore;
