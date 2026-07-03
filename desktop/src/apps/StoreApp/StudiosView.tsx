import { useCallback, useState } from "react";
import { Check, Loader2, Star } from "lucide-react";
import type { CatalogApp } from "./types";
import { useInstalledOptionalApps } from "@/hooks/use-installed-optional-apps";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { emitAppEvent, APP_OPTIONAL_CHANGED } from "@/lib/app-event-bus";

/* The studio catalog card id maps 1:1 to the registry app id, except Images
   Studio whose registry app id is "images". */
function studioAppId(studioId: string): string {
  return studioId === "images-studio" ? "images" : studioId;
}

/* ------------------------------------------------------------------
   Studio catalog entries (first-party, type "studio")
   ------------------------------------------------------------------ */

const STUDIOS: CatalogApp[] = [
  {
    id: "images-studio",
    name: "Images Studio",
    type: "studio",
    category: "Creative",
    version: "1.0.0",
    description: "Make and edit images on your own GPU, NPU, or CPU.",
    tagline: "Generate, edit, upscale",
    installed: true,
    compat: "green",
    studioState: "installed",
    cover: "radial-gradient(120% 120% at 30% 20%,#3a3357,transparent 60%),linear-gradient(140deg,#211d30,#14121b)",
  },
  {
    id: "game-studio",
    name: "Game Studio",
    type: "studio",
    category: "Creative",
    version: "1.0.0",
    description: "Describe a 3D game and play it in the browser. Runs entirely offline.",
    tagline: "Offline AI game maker",
    installed: true,
    compat: "green",
    studioState: "installed",
    cover: "radial-gradient(120% 120% at 70% 25%,#1f4a4f,transparent 60%),linear-gradient(140deg,#10242a,#0c181c)",
  },
  {
    id: "coding-studio",
    name: "Coding Studio",
    type: "studio",
    category: "Dev",
    version: "1.0.0",
    description: "An agent writes, runs, and previews your app on the cluster.",
    tagline: "Describe, build, preview",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 35% 25%,#34384a,transparent 60%),linear-gradient(140deg,#1b1d27,#121319)",
  },
  {
    id: "design-studio",
    name: "Design Studio",
    type: "studio",
    category: "Creative",
    version: "0.0.0",
    description: "Canva-style design with AI, on a shared canvas engine.",
    tagline: "Graphics and layouts",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 65% 20%,#4a3a4f,transparent 60%),linear-gradient(140deg,#241a27,#16121a)",
  },
  {
    id: "music-studio",
    name: "Music Studio",
    type: "studio",
    category: "Creative",
    version: "0.0.0",
    description: "Compose, arrange, and generate audio on your hardware.",
    tagline: "Web DAW with AI",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 30% 25%,#2f4a3a,transparent 60%),linear-gradient(140deg,#16271d,#101a14)",
  },
  {
    id: "app-studio",
    name: "App Studio",
    type: "studio",
    category: "Dev",
    version: "0.0.0",
    description: "Build and share new taOS apps, sandboxed and safe.",
    tagline: "taOS app builder",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 70% 25%,#3a4150,transparent 60%),linear-gradient(140deg,#1c1f28,#13151b)",
  },
  {
    id: "office-studio",
    name: "Office Studio",
    type: "studio",
    category: "Productivity",
    version: "0.0.0",
    description: "Documents, spreadsheets, and presentations with AI.",
    tagline: "Write, Calc, Slides",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 35% 20%,#4a4632,transparent 60%),linear-gradient(140deg,#262216,#181610)",
  },
  {
    id: "web-studio",
    name: "Web Studio",
    type: "studio",
    category: "Dev",
    version: "1.0.0",
    description: "Build sites from templates, edit sections visually, export static HTML.",
    tagline: "AI website builder",
    installed: false,
    compat: "green",
    studioState: "available",
    cover: "radial-gradient(120% 120% at 65% 25%,#324a47,transparent 60%),linear-gradient(140deg,#152624,#0f1817)",
  },
];

/* ------------------------------------------------------------------
   Community studios (static mock)
   ------------------------------------------------------------------ */

interface CommunityStudio {
  id: string;
  name: string;
  badge: "Fork" | "Layout";
  parent: string;
  description: string;
  stars: number;
  cover: string;
}

const COMMUNITY_STUDIOS: CommunityStudio[] = [
  {
    id: "pixel-art-studio",
    name: "Pixel Art Studio",
    badge: "Fork",
    parent: "Images Studio fork",
    description: "Palette-locked, grid-snapped sprite workflow.",
    stars: 1200,
    cover: "radial-gradient(120% 120% at 30% 20%,#3f3357,transparent 60%),linear-gradient(140deg,#221c30,#15111b)",
  },
  {
    id: "lofi-beats-kit",
    name: "Lo-fi Beats Kit",
    badge: "Layout",
    parent: "Music Studio layout",
    description: "Boom-bap drum rack, vinyl FX, swing presets.",
    stars: 860,
    cover: "radial-gradient(120% 120% at 70% 25%,#2f4a3a,transparent 60%),linear-gradient(140deg,#16271d,#101a14)",
  },
  {
    id: "api-forge",
    name: "API Forge",
    badge: "Layout",
    parent: "Coding Studio layout",
    description: "FastAPI scaffold, request runner, schema view.",
    stars: 2400,
    cover: "radial-gradient(120% 120% at 35% 25%,#3a4150,transparent 60%),linear-gradient(140deg,#1c1f28,#13151b)",
  },
  {
    id: "retro-fps-kit",
    name: "Retro FPS Kit",
    badge: "Fork",
    parent: "Game Studio fork",
    description: "Doom-style raycaster templates and assets.",
    stars: 970,
    cover: "radial-gradient(120% 120% at 65% 20%,#4a3340,transparent 60%),linear-gradient(140deg,#271620,#180f14)",
  },
];

/* ------------------------------------------------------------------
   Layout chips (static mock)
   ------------------------------------------------------------------ */

interface LayoutChip {
  id: string;
  name: string;
  parent: string;
  author: string;
  iconGradient: string;
}

const LAYOUT_CHIPS: LayoutChip[] = [
  {
    id: "photo-retoucher",
    name: "Photo Retoucher",
    parent: "Images Studio",
    author: "@mara",
    iconGradient: "linear-gradient(135deg,#6f7687,#474d5e)",
  },
  {
    id: "chiptune",
    name: "Chiptune",
    parent: "Music Studio",
    author: "@ben",
    iconGradient: "linear-gradient(135deg,#5f8a6f,#456f54)",
  },
  {
    id: "static-site-kit",
    name: "Static Site Kit",
    parent: "Coding Studio",
    author: "@ivo",
    iconGradient: "linear-gradient(135deg,#5d7a8a,#46606c)",
  },
];

function formatStars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/* ------------------------------------------------------------------
   Hero card for the featured studio (Coding Studio)
   ------------------------------------------------------------------ */

function StudioHero({
  studio,
  installed,
  onOpen,
  onInstall,
}: {
  studio: CatalogApp;
  installed: Set<string>;
  onOpen: (appId: string) => void;
  onInstall: (appId: string) => Promise<void>;
}) {
  const appId = studioAppId(studio.id);
  const isInstalled = installed.has(appId);
  const [busy, setBusy] = useState(false);
  const handleGet = useCallback(async () => {
    setBusy(true);
    try {
      await onInstall(appId);
    } catch {
      /* leave the button as Get so the user can retry */
    } finally {
      setBusy(false);
    }
  }, [appId, onInstall]);

  return (
    <div
      className="relative rounded-2xl overflow-hidden border border-shell-border-strong flex items-end"
      style={{ height: 228 }}
    >
      <div className="absolute inset-0" style={{ background: studio.cover }} />
      <div
        className="absolute inset-0"
        style={{ background: "linear-gradient(90deg,rgba(10,10,12,0.80) 0%,rgba(10,10,12,0.40) 55%,transparent 100%)" }}
      />
      <div className="relative p-7" style={{ maxWidth: "62%" }}>
        <div
          className="text-[11px] font-bold tracking-widest uppercase mb-2"
          style={{ color: "var(--color-accent)" }}
        >
          Featured -- New Studio
        </div>
        <h2 className="text-[28px] font-extrabold text-shell-text leading-tight tracking-tight">
          {studio.name}
        </h2>
        <p className="text-[13.5px] text-shell-text-secondary mt-2 leading-relaxed">
          {studio.description}
        </p>
        <div className="flex items-center gap-3 mt-4">
          <div
            className="w-12 h-12 rounded-2xl flex items-center justify-center text-white shrink-0"
            style={{ background: "linear-gradient(135deg,#6f7687,#474d5e)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} width={26} height={26}>
              <path d="m8 6-5 6 5 6M16 6l5 6-5 6" />
            </svg>
          </div>
          <button
            type="button"
            onClick={isInstalled ? () => onOpen(appId) : handleGet}
            disabled={busy}
            className="px-5 py-2 rounded-full text-[13px] font-bold text-white transition-colors disabled:opacity-60 inline-flex items-center gap-2"
            style={{ background: "var(--color-accent)" }}
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : isInstalled ? "Open" : "Get"}
          </button>
          <button
            type="button"
            onClick={() => onOpen(appId)}
            className="px-5 py-2 rounded-full text-[13px] font-bold text-shell-text bg-shell-surface-active hover:bg-white/10 transition-colors"
          >
            Preview
          </button>
          <span className="text-[12px] text-shell-text-tertiary">{studio.category} -- runs on cluster</span>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   Studio card (grid)
   ------------------------------------------------------------------ */

function StudioCard({
  studio,
  installed,
  onOpen,
  onInstall,
}: {
  studio: CatalogApp;
  installed: Set<string>;
  onOpen: (appId: string) => void;
  onInstall: (appId: string) => Promise<void>;
}) {
  const appId = studioAppId(studio.id);
  const isSoon = studio.studioState === "soon";
  // Bundled studios (Images, Game) ship with the OS; available ones become
  // installed once they are in the optional-app set.
  const isBundled = studio.studioState === "installed";
  const isInstalled = isBundled || installed.has(appId);
  const [busy, setBusy] = useState(false);
  const handleGet = useCallback(async () => {
    setBusy(true);
    try {
      await onInstall(appId);
    } catch {
      /* leave the button as Get so the user can retry */
    } finally {
      setBusy(false);
    }
  }, [appId, onInstall]);

  return (
    <div className="flex flex-col rounded-2xl border border-shell-border bg-shell-surface/60 overflow-hidden">
      {/* Cover */}
      <div className="relative flex items-center justify-center" style={{ height: 104, background: studio.cover }}>
        <span className="absolute top-2 left-2 text-[10px] font-bold tracking-wide uppercase px-2 py-0.5 rounded-full bg-black/40 backdrop-blur-sm text-white/80">
          {studio.category}
        </span>
        {isSoon && (
          <span
            className="absolute top-2 right-2 text-[10px] font-bold tracking-wide uppercase px-2 py-0.5 rounded-full text-white"
            style={{ background: "rgba(255,255,255,0.14)" }}
          >
            Soon
          </span>
        )}
      </div>
      {/* Meta */}
      <div className="flex flex-col gap-2 p-3.5 flex-1">
        <div>
          <div className="text-[14.5px] font-bold text-shell-text leading-snug">{studio.name}</div>
          <div className="text-[11.5px] text-shell-text-tertiary mt-0.5">{studio.tagline}</div>
        </div>
        <p className="text-[12px] text-shell-text-secondary leading-relaxed flex-1">{studio.description}</p>
        <div className="flex items-center justify-between mt-1">
          {isInstalled ? (
            <span className="flex items-center gap-1 text-[11.5px] text-emerald-400 font-semibold">
              <Check className="w-3 h-3" /> Installed
            </span>
          ) : isSoon ? (
            <span className="text-[11.5px] text-shell-text-tertiary">In design</span>
          ) : (
            <span className="text-[11.5px] text-shell-text-tertiary">New</span>
          )}
          {isInstalled ? (
            <button
              type="button"
              onClick={() => onOpen(appId)}
              className="px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors"
            >
              Open
            </button>
          ) : isSoon ? (
            <button
              type="button"
              disabled
              className="px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text-tertiary text-[12px] font-bold cursor-default"
            >
              Notify me
            </button>
          ) : (
            <button
              type="button"
              onClick={handleGet}
              disabled={busy}
              className="px-4 py-1.5 rounded-full text-[12px] font-bold text-white transition-colors disabled:opacity-60 inline-flex items-center gap-1.5"
              style={{ background: "var(--color-accent)" }}
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Get"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   Community studio card (horizontal scroll row)
   ------------------------------------------------------------------ */

function CommunityStudioCard({ item }: { item: CommunityStudio }) {
  return (
    <div
      className="flex flex-col rounded-2xl border border-shell-border bg-shell-surface/60 overflow-hidden shrink-0"
      style={{ width: 268 }}
    >
      <div className="relative flex items-center justify-center" style={{ height: 104, background: item.cover }}>
        <span className="absolute top-2 left-2 text-[10px] font-bold tracking-wide uppercase px-2 py-0.5 rounded-full bg-black/40 backdrop-blur-sm text-white/80">
          {item.badge}
        </span>
      </div>
      <div className="flex flex-col gap-2 p-3.5 flex-1">
        <div>
          <div className="text-[14px] font-bold text-shell-text leading-snug">{item.name}</div>
          <div className="text-[11.5px] text-shell-text-tertiary mt-0.5">{item.parent}</div>
        </div>
        <p className="text-[12px] text-shell-text-secondary leading-relaxed flex-1">{item.description}</p>
        <div className="flex items-center justify-between mt-1">
          <span className="flex items-center gap-1 text-[11.5px] text-shell-text-tertiary">
            <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
            {formatStars(item.stars)}
          </span>
          <button
            type="button"
            className="px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors"
          >
            Get
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   Layout chip
   ------------------------------------------------------------------ */

function LayoutChipCard({ chip }: { chip: LayoutChip }) {
  return (
    <div className="flex items-center gap-3 p-3.5 rounded-2xl bg-shell-surface border border-shell-border">
      <div
        className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 text-white"
        style={{ background: chip.iconGradient }}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-semibold text-shell-text leading-snug">{chip.name}</div>
        <div className="text-[11px] text-shell-text-tertiary mt-0.5">{chip.parent} -- by {chip.author}</div>
      </div>
      <button
        type="button"
        className="ml-auto shrink-0 px-3.5 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[11.5px] font-bold hover:bg-white/10 transition-colors"
      >
        Get
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------
   Section header
   ------------------------------------------------------------------ */

function SectionHeader({
  title,
  sub,
  action,
}: {
  title: string;
  sub?: string;
  action?: string;
}) {
  return (
    <div className="flex items-baseline justify-between mb-3.5">
      <div className="flex items-baseline gap-2.5">
        <h2 className="text-[19px] font-bold text-shell-text tracking-tight">{title}</h2>
        {sub && <span className="text-[12.5px] text-shell-text-tertiary">{sub}</span>}
      </div>
      {action && (
        <button type="button" className="text-[13px] text-accent hover:text-shell-text-secondary transition-colors">
          {action}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------
   StudiosView
   ------------------------------------------------------------------ */

export function StudiosView() {
  const hero = STUDIOS.find((s) => s.id === "coding-studio")!;
  const installed = useInstalledOptionalApps();
  const openWindow = useProcessStore((s) => s.openWindow);

  const openStudio = useCallback(
    (appId: string) => {
      const app = getApp(appId);
      if (app) openWindow(appId, app.defaultSize);
    },
    [openWindow],
  );

  const installStudio = useCallback(async (appId: string) => {
    const res = await fetch(`/api/apps/optional/${encodeURIComponent(appId)}/install`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`install failed (${res.status})`);
    // Re-fetch the installed set so the card flips to Open and the launcher
    // surfaces the studio at once.
    emitAppEvent(APP_OPTIONAL_CHANGED, appId);
  }, []);

  return (
    <div className="flex flex-col gap-8 pb-8">
      {/* Hero */}
      <StudioHero studio={hero} installed={installed} onOpen={openStudio} onInstall={installStudio} />

      {/* taOS Studios grid */}
      <section aria-label="taOS Studios">
        <SectionHeader
          title="taOS Studios"
          sub="first-party, built and maintained by taOS"
          action="About studios"
        />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
          {STUDIOS.map((s) => (
            <StudioCard key={s.id} studio={s} installed={installed} onOpen={openStudio} onInstall={installStudio} />
          ))}
        </div>
      </section>

      {/* Community Studios */}
      <section aria-label="Community Studios">
        <SectionHeader
          title="Community Studios"
          sub="made by people, shared like apps"
          action="See all"
        />
        <div className="flex gap-4 overflow-x-auto pb-2" style={{ scrollbarWidth: "none" }}>
          {COMMUNITY_STUDIOS.map((item) => (
            <CommunityStudioCard key={item.id} item={item} />
          ))}
        </div>
      </section>

      {/* Studio layouts */}
      <section aria-label="Studio layouts">
        <SectionHeader
          title="Studio layouts"
          sub="tune a studio, save the layout, share it"
          action="Share yours"
        />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3.5">
          {LAYOUT_CHIPS.map((chip) => (
            <LayoutChipCard key={chip.id} chip={chip} />
          ))}
        </div>
      </section>
    </div>
  );
}

export default StudiosView;
