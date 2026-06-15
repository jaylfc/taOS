import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Search, Download, Trash2, Check, Package, Loader2, Server,
  Compass, Grid2x2, Bot, Brain, Plug, Wrench, Star, Globe,
  ArrowDownToLine, RefreshCw, Users, Cpu,
} from "lucide-react";
import { Input } from "@/components/ui";
import { fetchLatestFrameworks, LatestVersion } from "@/lib/framework-api";
import type { CatalogApp, InstallTarget, InstalledEntry } from "./types";
import { DevicePillBar, UnknownHardwareBanner } from "./DevicePillBar";
import { BackendPillBar } from "./BackendPillBar";
import { IncompatibleToggle } from "./IncompatibleToggle";
import { filterCatalog, compatFromResolver, hasUnknownHardwareDevice } from "./filter";
import { resolveModel, type ResolveResponse } from "./resolver-types";
import { compatVisuals } from "./compat-visuals";
import { loadFilter, saveFilter } from "./storage";
import { emitAppEvent, APP_INSTALLED } from "@/lib/app-event-bus";
import { TaosAppsSection } from "./TaosAppsSection";

/* ------------------------------------------------------------------
   Dashboard-icons CDN helper
   ------------------------------------------------------------------ */

const di = (slug: string) =>
  `https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/${slug}.png`;

/* ------------------------------------------------------------------
   Nav sections
   ------------------------------------------------------------------ */

type NavId =
  | "discover"
  | "apps"
  | "agents"
  | "models"
  | "services"
  | "mcp"
  | "devtools"
  | "community"
  | "installed"
  | "updates";

interface NavItem {
  id: NavId;
  label: string;
  icon: React.ReactNode;
  group?: string;
}

const NAV: NavItem[] = [
  { id: "discover",  label: "Discover",    icon: <Compass size={15} /> },
  { id: "apps",      label: "Apps",        icon: <Grid2x2 size={15} /> },
  { id: "agents",    label: "Agents",      icon: <Bot size={15} /> },
  { id: "models",    label: "Models",      icon: <Brain size={15} /> },
  { id: "services",  label: "Services",    icon: <Globe size={15} /> },
  { id: "mcp",       label: "MCP Servers", icon: <Plug size={15} /> },
  { id: "devtools",  label: "Dev Tools",   icon: <Wrench size={15} /> },
  { id: "community", label: "Community",   icon: <Users size={15} /> },
  { id: "installed", label: "Installed",   icon: <ArrowDownToLine size={15} />, group: "Library" },
  { id: "updates",   label: "Updates",     icon: <RefreshCw size={15} />, group: "Library" },
];

/* ------------------------------------------------------------------
   Homelab catalog additions with real star counts + dashboard-icons
   ------------------------------------------------------------------ */

const HOMELAB_APPS: CatalogApp[] = [
  {
    id: "home-assistant", name: "Home Assistant", type: "home",
    version: "latest", description: "Run your whole smart home locally. 2,000+ integrations, no cloud.",
    tagline: "Open-source home automation platform",
    installed: false, compat: "green",
    repo: "home-assistant/core", iconSlug: "home-assistant", stars: 72400,
    cover: "radial-gradient(120% 120% at 30% 20%,#16607a,transparent 60%),linear-gradient(140deg,#0e2230,#0a1620)",
    category: "home",
  },
  {
    id: "immich", name: "Immich", type: "home",
    version: "latest", description: "Your own Google Photos. AI search, face grouping, phone backup.",
    tagline: "Self-hosted photo and video library",
    installed: false, compat: "green",
    repo: "immich-app/immich", iconSlug: "immich", stars: 50100,
    cover: "radial-gradient(120% 120% at 70% 30%,#5a2f7a,transparent 60%),linear-gradient(140deg,#21142b,#150d1a)",
    category: "home",
  },
  {
    id: "jellyfin", name: "Jellyfin", type: "home",
    version: "latest", description: "Stream your movies, shows and music to any device. Zero fees.",
    tagline: "Free and open source media server",
    installed: false, compat: "green",
    repo: "jellyfin/jellyfin", iconSlug: "jellyfin", stars: 35000,
    cover: "radial-gradient(120% 120% at 40% 30%,#1f4d63,transparent 60%),linear-gradient(140deg,#10222a,#0b161b)",
    category: "home",
  },
  {
    id: "vaultwarden", name: "Vaultwarden", type: "home",
    version: "latest", description: "Self-hosted Bitwarden. Your passwords never leave the house.",
    tagline: "Unofficial Bitwarden-compatible server",
    installed: false, compat: "green",
    repo: "dani-garcia/vaultwarden", iconSlug: "vaultwarden", stars: 42000,
    cover: "radial-gradient(120% 120% at 60% 25%,#1f5a3a,transparent 60%),linear-gradient(140deg,#12261b,#0c1712)",
    category: "home",
  },
  {
    id: "sonarr", name: "Sonarr", type: "home",
    version: "latest", description: "TV series library manager and downloader automation.",
    tagline: "Smart PVR for Usenet and BitTorrent users",
    installed: false, compat: "green",
    repo: "Sonarr/Sonarr", iconSlug: "sonarr", stars: 11200,
    category: "home",
  },
  {
    id: "radarr", name: "Radarr", type: "home",
    version: "latest", description: "Movie collection manager and download automator.",
    tagline: "Fork of Sonarr to work with movies",
    installed: false, compat: "green",
    repo: "Radarr/Radarr", iconSlug: "radarr", stars: 9600,
    category: "home",
  },
  {
    id: "qbittorrent", name: "qBittorrent", type: "home",
    version: "latest", description: "Fast and lightweight BitTorrent client with web UI.",
    tagline: "Open-source software alternative to uTorrent",
    installed: false, compat: "green",
    repo: "qbittorrent/qBittorrent", iconSlug: "qbittorrent", stars: 27600,
    category: "home",
  },
  {
    id: "sabnzbd", name: "SABnzbd", type: "home",
    version: "latest", description: "The automated Usenet download application.",
    tagline: "Open source binary newsreader",
    installed: false, compat: "green",
    repo: "sabnzbd/sabnzbd", iconSlug: "sabnzbd", stars: 2600,
    category: "home",
  },
  {
    id: "homebridge", name: "Homebridge", type: "home",
    version: "latest", description: "Bring non-native devices into Apple Home via HomeKit.",
    tagline: "HomeKit support for non-native accessories",
    installed: false, compat: "green",
    repo: "homebridge/homebridge", iconSlug: "homebridge", stars: 24500,
    category: "home",
  },
  {
    id: "adguard-home", name: "AdGuard Home", type: "infrastructure",
    version: "latest", description: "Network-wide ad and tracker blocking. No cloud dependency.",
    tagline: "Network-wide software for blocking ads",
    installed: false, compat: "green",
    repo: "AdguardTeam/AdGuardHome", iconSlug: "adguard-home", stars: 26800,
    category: "infrastructure",
  },
  {
    id: "uptime-kuma", name: "Uptime Kuma", type: "monitoring",
    version: "latest", description: "Self-hosted monitoring tool -- track uptime for services and APIs.",
    tagline: "Fancy self-hosted monitoring tool",
    installed: false, compat: "green",
    repo: "louislam/uptime-kuma", iconSlug: "uptime-kuma", stars: 60300,
    category: "monitoring",
  },
  {
    id: "nextcloud", name: "Nextcloud", type: "productivity",
    version: "latest", description: "Files, calendar, contacts and office suite in one open-source platform.",
    tagline: "The most popular self-hosted collaboration platform",
    installed: false, compat: "green",
    repo: "nextcloud/server", iconSlug: "nextcloud", stars: 28100,
    category: "productivity",
  },
];

/* ------------------------------------------------------------------
   Mock catalog (used when /api/store/catalog is unreachable)
   ------------------------------------------------------------------ */

const MOCK_APPS: CatalogApp[] = [
  // Agent Frameworks
  { id: "smolagents",       name: "SmolAgents",        type: "agent-framework", version: "1.0.0", description: "HuggingFace code-based agents -- well-documented, 26k stars", installed: false, compat: "green", stars: 26000 },
  { id: "pocketflow",       name: "PocketFlow",        type: "agent-framework", version: "1.0.0", description: "Minimal 100-line framework, zero deps, graph-based", installed: false, compat: "green" },
  { id: "openclaw",         name: "OpenClaw",          type: "agent-framework", version: "1.0.0", description: "Full-featured multi-channel agent framework", installed: true,  compat: "green" },
  { id: "langroid",         name: "Langroid",          type: "agent-framework", version: "1.0.0", description: "Multi-agent message-passing framework", installed: false, compat: "green" },
  { id: "openai-agents-sdk",name: "OpenAI Agents SDK", type: "agent-framework", version: "1.0.0", description: "Provider-agnostic agent SDK from OpenAI", installed: false, compat: "green" },
  // Models
  { id: "qwen3-4b",  name: "Qwen3 4B",  type: "model", version: "3.0.0", description: "Good balance of speed and capability for most tasks", installed: true,  compat: "green" },
  { id: "qwen3-1.7b",name: "Qwen3 1.7B",type: "model", version: "3.0.0", description: "Fast, fits comfortably in 8GB RAM", installed: false, compat: "green" },
  { id: "qwen3-8b",  name: "Qwen3 8B",  type: "model", version: "3.0.0", description: "Most capable local model for 16GB devices", installed: false, compat: "yellow" },
  // MCP Servers
  { id: "mcp-pandoc",       name: "MCP Pandoc",      type: "mcp", version: "0.1.0", description: "Document format conversion -- markdown, docx, pdf, 30+ formats", installed: false, compat: "green" },
  { id: "mcp-server-office",name: "MCP Office Docs", type: "mcp", version: "0.1.0", description: "Read, write, and edit .docx files programmatically", installed: false, compat: "green" },
  { id: "playwright-mcp",   name: "Playwright MCP",  type: "mcp", version: "1.0.0", description: "Browser automation for agents via Playwright", installed: false, compat: "green" },
  { id: "github-mcp-server",name: "GitHub MCP",      type: "mcp", version: "1.0.0", description: "Issues, PRs, repos, search -- official GitHub MCP", installed: false, compat: "green" },
  { id: "mcp-memory",       name: "MCP Memory",      type: "mcp", version: "1.0.0", description: "Knowledge graph memory for persistent context", installed: false, compat: "green" },
  // Plugins
  { id: "web-search",          name: "Web Search",       type: "plugin", version: "0.3.0", description: "Search the web via SearXNG or Perplexica", installed: false, compat: "green" },
  { id: "image-generation-tool",name: "Image Generation", type: "plugin", version: "0.1.0", description: "Generate images via Stable Diffusion", installed: false, compat: "green" },
  // Services
  { id: "searxng", name: "SearXNG", type: "service", category: "infrastructure", version: "latest", description: "Privacy-respecting metasearch engine", installed: false, compat: "green" },
  { id: "gitea",   name: "Gitea",   type: "service", category: "dev-tool",       version: "latest", description: "Lightweight self-hosted Git service", installed: false, compat: "green" },
  { id: "n8n",     name: "n8n",     type: "service", category: "automation",     version: "latest", description: "Workflow automation platform", installed: false, compat: "green", iconSlug: "n8n" },
  // Streaming apps
  { id: "code-server-kasm", name: "Code Server (Streamed)", type: "streaming-app", version: "latest", description: "VS Code in the browser via KasmVNC", installed: false, compat: "green" },
  { id: "blender",           name: "Blender",               type: "streaming-app", version: "latest", description: "3D creation suite streamed via KasmVNC", installed: false, compat: "yellow" },
  { id: "libreoffice",       name: "LibreOffice",           type: "streaming-app", version: "latest", description: "Full office suite streamed via KasmVNC", installed: false, compat: "green" },
  // Image gen
  {
    id: "comfyui", name: "ComfyUI", type: "image-gen", version: "latest",
    description: "Node-based visual pipelines for image, video and audio generation. Runs on your cluster, drives any model you've installed.",
    tagline: "Node-based Stable Diffusion workflow editor",
    installed: false, compat: "yellow",
    iconSlug: "comfyui",
    cover: "radial-gradient(120% 140% at 12% 18%,#3a2d5e,transparent 55%),radial-gradient(120% 130% at 85% 80%,#1e4d63,transparent 55%),linear-gradient(120deg,#20202a,#14141a)",
  },
  { id: "fooocus", name: "Fooocus", type: "image-gen", version: "latest", description: "Simple Stable Diffusion with minimal setup", installed: false, compat: "yellow" },
  // Audio / video / devtools / infra
  { id: "kokoro-tts",    name: "Kokoro TTS",    type: "voice",    version: "latest", description: "High-quality text-to-speech", installed: false, compat: "green" },
  { id: "whisper-stt",   name: "Whisper STT",   type: "voice",    version: "latest", description: "OpenAI Whisper speech-to-text", installed: false, compat: "green" },
  { id: "animatediff",   name: "AnimateDiff",   type: "video-gen",version: "latest", description: "AI video generation from text and images", installed: false, compat: "yellow" },
  { id: "corridorkey",   name: "CorridorKey",   type: "video-gen",version: "latest", description: "AI video generation via ComfyUI workflows", installed: false, compat: "yellow" },
  { id: "code-server",   name: "Code Server",   type: "dev-tool", version: "latest", description: "VS Code in the browser -- remote development environment", installed: false, compat: "green" },
  { id: "jupyter-lab",   name: "JupyterLab",    type: "dev-tool", version: "latest", description: "Interactive notebooks for data science and experimentation", installed: false, compat: "green" },
  { id: "tailscale",     name: "Tailscale",     type: "infrastructure", version: "latest", description: "Zero-config mesh VPN for secure networking between devices", installed: false, compat: "green", iconSlug: "tailscale" },
  { id: "caddy",         name: "Caddy",         type: "infrastructure", version: "latest", description: "Automatic HTTPS reverse proxy and web server", installed: false, compat: "green" },
  ...HOMELAB_APPS,
];

/* ------------------------------------------------------------------
   Community showcase items (static mock -- no backend yet)
   ------------------------------------------------------------------ */

interface CommunityItem {
  id: string;
  name: string;
  badge: string;
  author: string;
  description: string;
  installs: number;
  icon: React.ReactNode;
  cover: string;
}

const COMMUNITY_ITEMS: CommunityItem[] = [
  {
    id: "matrix-terminal",
    name: "Matrix Terminal",
    badge: "Theme",
    author: "@neo",
    description: "Phosphor-green terminal theme with a code-rain wallpaper.",
    installs: 1240,
    icon: <span style={{ fontFamily: "ui-monospace,monospace", fontSize: 14, color: "#39ff88" }}>&gt;_</span>,
    cover: "radial-gradient(120% 120% at 35% 25%,#103a2a,transparent 60%),linear-gradient(140deg,#0c1a14,#08120d)",
  },
  {
    id: "aurora-drift",
    name: "Aurora Drift",
    badge: "Live Wallpaper",
    author: "@ria",
    description: "Slow-flowing aurora ribbons that adapt to any screen.",
    installs: 842,
    icon: <span style={{ fontSize: 18, background: "linear-gradient(150deg,#5ad0ff,#7b5cff)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>✺</span>,
    cover: "radial-gradient(120% 120% at 60% 25%,#2a3f7a,transparent 60%),linear-gradient(140deg,#141a2b,#0d1119)",
  },
  {
    id: "habit-garden",
    name: "Habit Garden",
    badge: "App",
    author: "@sol",
    description: "Grow a plant as you keep streaks. Built with the App Builder.",
    installs: 3110,
    icon: <span style={{ fontSize: 18, color: "#ffb340" }}>❀</span>,
    cover: "radial-gradient(120% 120% at 40% 25%,#5a3a1f,transparent 60%),linear-gradient(140deg,#231811,#16100a)",
  },
  {
    id: "standup-bot",
    name: "Standup Bot",
    badge: "Agent",
    author: "@max",
    description: "Collects daily updates from your agents and posts a digest.",
    installs: 567,
    icon: <span style={{ fontSize: 18, background: "linear-gradient(150deg,#bf8cff,#8b5cff)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>◆</span>,
    cover: "radial-gradient(120% 120% at 55% 25%,#4a2a5e,transparent 60%),linear-gradient(140deg,#1c1426,#120c18)",
  },
];

/* ------------------------------------------------------------------
   Agent framework showcase for "Build agents with" section
   ------------------------------------------------------------------ */

interface FrameworkItem {
  id: string;
  name: string;
  sub: string;
  iconStyle: React.CSSProperties;
  iconChar: string;
  installed?: boolean;
  stars?: number;
}

const FRAMEWORK_ITEMS: FrameworkItem[] = [
  { id: "openclaw",  name: "OpenClaw",  sub: "Agent Framework · ACP", iconStyle: { background: "linear-gradient(150deg,#ff7a5c,#ff5b3d)" }, iconChar: "◆", installed: true },
  { id: "hermes",    name: "Hermes",    sub: "Agent Framework",        iconStyle: { background: "linear-gradient(150deg,#8b5cff,#6a3dff)" }, iconChar: "⬡" },
  { id: "pocketflow",name: "PocketFlow",sub: "Agent Framework",        iconStyle: { background: "linear-gradient(150deg,#41d0a3,#27a982)" }, iconChar: "⟡" },
  { id: "smolagents",name: "SmolAgents",sub: "Agent Framework",        iconStyle: { background: "linear-gradient(150deg,#9aa0ad,#6e7686)" }, iconChar: "⬢", stars: 26000 },
];

/* ------------------------------------------------------------------
   Icon resolution: dashboard-icons CDN slug takes priority, then
   existing APP_ICONS map, then derived family fallbacks.
   ------------------------------------------------------------------ */

const si = (slug: string): string => `/static/store-icons/brands/${slug}.svg`;
const gh = (owner: string): string => `https://github.com/${owner}.png?size=96`;

const APP_ICONS: Record<string, string> = {
  // Agent frameworks
  "smolagents": gh("huggingface"), "pocketflow": gh("The-Pocket"),
  "openclaw": "/static/store-icons/openclaw.jpg",
  "openai-agents-sdk": si("openai"), "langroid": gh("langroid"),
  // Models
  "qwen3-4b": gh("QwenLM"), "qwen3-1.7b": gh("QwenLM"), "qwen3-8b": gh("QwenLM"),
  "llama-3.1-8b": si("meta"), "llama-3.2-1b": si("meta"),
  "gemma-3-4b": si("googlegemini"),
  // MCP / plugins
  "github-mcp-server": si("github"), "playwright-mcp": si("playwright"),
  "mcp-memory": gh("modelcontextprotocol"),
  // Services
  "searxng": si("searxng"), "gitea": si("gitea"), "n8n": si("n8n"),
  "code-server": gh("coder"), "code-server-kasm": gh("coder"),
  "blender": si("blender"), "libreoffice": si("libreoffice"),
  "jupyter-lab": si("jupyter"), "tailscale": si("tailscale"),
  "caddy": gh("caddyserver"), "animatediff": gh("guoyww"),
  "comfyui": gh("comfyanonymous"), "fooocus": gh("lllyasviel"),
  "kokoro-tts": gh("hexgrad"), "whisper-stt": si("openai"),
  // Homelab -- dashboard-icons CDN is handled via iconSlug on the app object;
  // listing them here as fallback for when catalog comes from the API without iconSlug.
  "home-assistant": si("homeassistant"), "uptime-kuma": si("uptimekuma"),
};

function resolveIconUrl(app: CatalogApp): string | null {
  if (app.iconSlug) return di(app.iconSlug);
  if (APP_ICONS[app.id]) return APP_ICONS[app.id] ?? null;
  // Family fallbacks
  if (app.id.startsWith("qwen")) return gh("QwenLM");
  if (app.id.startsWith("llama")) return si("meta");
  if (app.id.startsWith("gemma")) return si("googlegemini");
  if (app.id.startsWith("phi-")) return gh("microsoft");
  if (app.id.startsWith("whisper")) return si("openai");
  if (app.id.startsWith("deepseek")) return gh("deepseek-ai");
  if (app.id.startsWith("mistral") || app.id.startsWith("mixtral")) return gh("mistralai");
  if (app.id.startsWith("flux-")) return gh("black-forest-labs");
  if (app.id.startsWith("sd-") || app.id.startsWith("sdxl") || app.id.startsWith("sd3")) return gh("Stability-AI");
  return null;
}

function formatStars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/* ------------------------------------------------------------------
   AppCard -- used in grid views (non-discover sections)
   ------------------------------------------------------------------ */

function AppCard({
  app, affected, onInstall, onUninstall, installTargets, runtimeHost, defaultTargetRemote, resolveResponse,
}: {
  app: CatalogApp;
  affected: number;
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
  installTargets: InstallTarget[];
  runtimeHost: string | null;
  defaultTargetRemote?: string;
  resolveResponse?: ResolveResponse;
}) {
  const [busy, setBusy] = useState(false);
  const [iconFailed, setIconFailed] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState<string>(defaultTargetRemote ?? "local");
  const [selectedVariant, setSelectedVariant] = useState<string>("auto");
  const [error, setError] = useState<string | null>(null);
  interface ProgressSnap { state: string; percent: number | null; bytes_downloaded: number; bytes_total: number; detail: string; error: string | null; }
  const [progress, setProgress] = useState<ProgressSnap | null>(null);

  useEffect(() => { if (defaultTargetRemote !== undefined) setSelectedTarget(defaultTargetRemote); }, [defaultTargetRemote]);

  useEffect(() => {
    if (!busy) return;
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const r = await fetch(`/api/store/install-progress/by-app/${encodeURIComponent(app.id)}`, { headers: { Accept: "application/json" } });
          if (r.ok) {
            const j = await r.json();
            const a = j?.active;
            if (a) {
              setProgress({ state: a.state, percent: a.percent ?? null, bytes_downloaded: a.bytes_downloaded ?? 0, bytes_total: a.bytes_total ?? 0, detail: a.detail ?? "", error: a.error ?? null });
              if (a.state === "installed" || a.state === "failed" || a.state === "cancelled") break;
            }
          }
        } catch { /* network blip */ }
        await new Promise((res) => setTimeout(res, 1500));
      }
    };
    void poll();
    return () => { cancelled = true; };
  }, [busy, app.id]);

  const iconUrl = resolveIconUrl(app);
  const variantOptions = app.variants ?? [];
  const showVariantPicker = !app.installed && variantOptions.length > 1;
  const showTargetPicker = !app.installed && installTargets.length > 1;

  const handleAction = async () => {
    setBusy(true); setError(null); setProgress(null);
    try {
      if (app.installed) {
        const res = await fetch("/api/store/uninstall", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ app_id: app.id }) });
        if (!res.ok) { let msg = `Uninstall failed (${res.status})`; try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ } setError(msg); setBusy(false); return; }
        onUninstall(app.id);
      } else {
        const body: Record<string, unknown> = { app_id: app.id, target_remote: selectedTarget };
        if (selectedVariant !== "auto") body.variant_id = selectedVariant;
        const res = await fetch("/api/store/install-v2", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
        if (!res.ok) { let msg = `Install failed (${res.status})`; try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ } setError(msg); setBusy(false); return; }
        onInstall(app.id);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Network error"); }
    setBusy(false);
    setTimeout(() => setProgress(null), 1500);
  };

  const visuals = compatVisuals(resolveResponse);
  const appType = app.category || app.type;

  return (
    <div
      className={`flex flex-col rounded-2xl border transition-all duration-200 hover:-translate-y-0.5 overflow-hidden ${visuals.borderClass} bg-shell-surface/60`}
      style={{ borderColor: undefined }}
      title={visuals.tooltip || undefined}
    >
      {/* Cover strip */}
      <div
        className="h-20 relative shrink-0"
        style={{ background: app.cover ?? "linear-gradient(140deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01))" }}
      >
        <span className="absolute top-2 left-2 text-[10px] font-bold tracking-wide uppercase px-2 py-0.5 rounded-full bg-black/40 backdrop-blur-sm text-white/80">
          {appType}
        </span>
      </div>
      {/* Meta */}
      <div className="flex flex-col gap-2 p-3 flex-1">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 overflow-hidden bg-white/[0.06]" style={{ padding: 5 }}>
            {iconUrl && !iconFailed
              ? <img src={iconUrl} alt="" className="w-full h-full object-contain" onError={() => setIconFailed(true)} loading="lazy" />
              : <Package className="w-4 h-4 text-white/50" />}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[13px] font-semibold text-shell-text truncate leading-snug">{app.name}</span>
              {app.installed && <Check className="w-3 h-3 text-emerald-400 shrink-0" />}
              {affected > 0 && <span className="bg-yellow-700/30 text-yellow-200 text-[10px] px-1.5 py-0.5 rounded shrink-0">Update</span>}
            </div>
            <span className="text-[11px] text-shell-text-tertiary leading-none">v{app.version}</span>
          </div>
        </div>
        <p className="text-[11.5px] text-shell-text-secondary leading-relaxed flex-1">{app.description}</p>
        <div className="flex items-center justify-between">
          {app.stars ? (
            <span className="flex items-center gap-1 text-[11px] text-shell-text-tertiary">
              <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
              {formatStars(app.stars)}
            </span>
          ) : <span />}
        </div>
      </div>
      {/* Footer */}
      <div className="px-3 pb-3 flex flex-col gap-1.5">
        {error && <div role="alert" className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">{error}</div>}
        {progress && (
          <div className="flex flex-col gap-1" aria-live="polite">
            <div className="flex items-center justify-between text-[11px] text-shell-text-tertiary">
              <span className="capitalize">{progress.state.replace(/_/g, " ")}</span>
              <span>{progress.percent !== null ? `${progress.percent.toFixed(0)}%` : progress.bytes_downloaded > 0 ? `${(progress.bytes_downloaded / (1024 * 1024)).toFixed(1)} MB` : ""}</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-white/5 overflow-hidden" role="progressbar" aria-valuenow={progress.percent ?? 0} aria-valuemin={0} aria-valuemax={100}>
              <div className={`h-full transition-all ${progress.state === "failed" ? "bg-red-400" : progress.state === "installed" ? "bg-emerald-400" : "bg-sky-400"} ${progress.percent === null ? "animate-pulse w-1/3" : ""}`} style={{ width: progress.percent !== null ? `${progress.percent}%` : undefined }} />
            </div>
            {progress.detail && <span className="text-[10px] text-shell-text-tertiary truncate">{progress.detail}</span>}
          </div>
        )}
        {showTargetPicker && (
          <div className="flex items-center gap-2">
            <label htmlFor={`target-${app.id}`} className="text-[11px] text-shell-text-tertiary whitespace-nowrap">Install on</label>
            <select id={`target-${app.id}`} value={selectedTarget} onChange={(e) => setSelectedTarget(e.target.value)} className="flex-1 h-7 rounded-md border border-white/10 bg-shell-bg-deep px-2 text-[11px] text-shell-text focus-visible:outline-none" aria-label="Install target host">
              {installTargets.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
            </select>
          </div>
        )}
        {showVariantPicker && (
          <div className="flex items-center gap-2">
            <label htmlFor={`variant-${app.id}`} className="text-[11px] text-shell-text-tertiary whitespace-nowrap">Variant</label>
            <select id={`variant-${app.id}`} value={selectedVariant} onChange={(e) => setSelectedVariant(e.target.value)} className="flex-1 h-7 rounded-md border border-white/10 bg-shell-bg-deep px-2 text-[11px] text-shell-text focus-visible:outline-none" aria-label="Variant">
              <option value="auto">Auto (recommended)</option>
              {variantOptions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </div>
        )}
        {app.installed && runtimeHost && (
          <p className="text-[10px] text-shell-text-tertiary flex items-center gap-1">
            <Server className="w-3 h-3 shrink-0" />
            {runtimeHost === "127.0.0.1" ? "on controller" : `on ${runtimeHost}`}
          </p>
        )}
        <button
          type="button"
          onClick={handleAction}
          disabled={busy}
          aria-label={app.installed ? `Uninstall ${app.name}` : `Install ${app.name}`}
          className={`w-full flex items-center justify-center gap-1.5 py-1.5 rounded-full text-[12px] font-bold transition-colors ${app.installed ? "bg-red-500/15 text-red-400 hover:bg-red-500/25" : "bg-shell-surface-active text-shell-text hover:bg-white/10"}`}
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : app.installed ? <><Trash2 className="w-3.5 h-3.5" /> Uninstall</> : <><Download className="w-3.5 h-3.5" /> Install</>}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   RichCard -- hero-row cards with cover + logo + stars
   ------------------------------------------------------------------ */

function RichCard({
  app, onInstall, installTargets,
}: {
  app: CatalogApp;
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  const [busy, setBusy] = useState(false);
  const [iconFailed, setIconFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const iconUrl = resolveIconUrl(app);

  const handleGet = async () => {
    if (app.installed) return;
    setBusy(true); setError(null);
    try {
      // Read the current install target at click time so it tracks the device
      // selection, rather than a value captured at mount.
      const target = installTargets[0]?.name ?? "local";
      const res = await fetch("/api/store/install-v2", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ app_id: app.id, target_remote: target }) });
      if (!res.ok) { let msg = `Install failed (${res.status})`; try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ } setError(msg); setBusy(false); return; }
      onInstall(app.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Network error"); }
    setBusy(false);
  };

  const handleOpen = () => {
    // Future: launch the app window
  };

  return (
    <div className="flex flex-col rounded-2xl border border-shell-border bg-shell-surface/60 overflow-hidden shrink-0" style={{ width: 264 }}>
      {/* Cover */}
      <div className="h-28 relative shrink-0" style={{ background: app.cover ?? "linear-gradient(140deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01))" }}>
        <span className="absolute top-2 left-2 text-[10px] font-bold tracking-wide uppercase px-2 py-0.5 rounded-full bg-black/40 backdrop-blur-sm text-white/80">
          {app.category || app.type}
        </span>
      </div>
      {/* Meta */}
      <div className="flex flex-col gap-2 p-3 flex-1">
        <div className="flex items-center gap-2.5">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 overflow-hidden bg-white/[0.06]" style={{ padding: 6 }}>
            {iconUrl && !iconFailed
              ? <img src={iconUrl} alt="" className="w-full h-full object-contain" onError={() => setIconFailed(true)} loading="lazy" />
              : <Package className="w-5 h-5 text-white/50" />}
          </div>
          <div>
            <div className="text-[14px] font-semibold text-shell-text leading-snug">{app.name}</div>
            <div className="text-[11.5px] text-shell-text-tertiary">{app.tagline ?? (app.category || app.type)}</div>
          </div>
        </div>
        <p className="text-[12px] text-shell-text-secondary leading-relaxed flex-1">{app.description}</p>
        {error && <div className="text-[10px] text-red-300">{error}</div>}
        <div className="flex items-center justify-between">
          {app.installed
            ? <span className="flex items-center gap-1 text-[11.5px] text-emerald-400 font-semibold"><Check className="w-3 h-3" /> Installed</span>
            : app.stars
              ? <span className="flex items-center gap-1 text-[11.5px] text-shell-text-tertiary"><Star className="w-3 h-3 fill-amber-400 text-amber-400" />{formatStars(app.stars)}</span>
              : <span />}
          <button
            type="button"
            onClick={app.installed ? handleOpen : handleGet}
            disabled={busy}
            className="px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors"
          >
            {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : app.installed ? "Open" : "Get"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   SubscriptionRow -- compact 2-col list for "Replace your subs"
   ------------------------------------------------------------------ */

function SubscriptionRow({
  app, onInstall, installTargets,
}: {
  app: CatalogApp;
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  const [busy, setBusy] = useState(false);
  const [iconFailed, setIconFailed] = useState(false);
  const iconUrl = resolveIconUrl(app);

  const handleGet = async () => {
    if (app.installed) return;
    setBusy(true);
    try {
      const res = await fetch("/api/store/install-v2", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ app_id: app.id, target_remote: installTargets[0]?.name ?? "local" }) });
      if (res.ok) onInstall(app.id);
    } catch { /* ignore */ }
    setBusy(false);
  };

  return (
    <div className="flex items-center gap-3 px-2.5 py-2.5 rounded-xl hover:bg-shell-surface transition-colors">
      <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 overflow-hidden bg-white/[0.06]" style={{ padding: 7 }}>
        {iconUrl && !iconFailed
          ? <img src={iconUrl} alt="" className="w-full h-full object-contain" onError={() => setIconFailed(true)} loading="lazy" />
          : <Package className="w-5 h-5 text-white/50" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13.5px] font-semibold text-shell-text">{app.name}</div>
        <div className="text-[11.5px] text-shell-text-tertiary">
          {app.tagline ?? (app.category || app.type)}
          {app.stars ? ` · ★ ${formatStars(app.stars)}` : ""}
        </div>
      </div>
      {app.installed
        ? <span className="flex items-center gap-1 text-[11.5px] text-emerald-400 font-semibold ml-auto"><Check className="w-3 h-3" /> Installed</span>
        : <button
            type="button"
            onClick={handleGet}
            disabled={busy}
            className="ml-auto px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors"
          >
            {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : "Get"}
          </button>}
    </div>
  );
}

/* ------------------------------------------------------------------
   CommunityCard
   ------------------------------------------------------------------ */

function CommunityCard({ item }: { item: CommunityItem }) {
  return (
    <div className="flex flex-col rounded-2xl border border-shell-border bg-shell-surface/60 overflow-hidden shrink-0" style={{ width: 264 }}>
      <div className="h-28 relative shrink-0" style={{ background: item.cover }} />
      <div className="flex flex-col gap-2 p-3 flex-1">
        <div className="flex items-center gap-2.5">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 bg-white/[0.06]">
            {item.icon}
          </div>
          <div>
            <div className="text-[14px] font-semibold text-shell-text leading-snug">{item.name}</div>
            <div className="text-[11.5px] text-shell-text-tertiary">{item.badge} · by {item.author}</div>
          </div>
        </div>
        <p className="text-[12px] text-shell-text-secondary leading-relaxed flex-1">{item.description}</p>
        <div className="flex items-center justify-between">
          <span className="text-[11.5px] text-shell-text-tertiary">
            <ArrowDownToLine className="w-3 h-3 inline mr-1" />{item.installs.toLocaleString()} installs
          </span>
          <button type="button" className="px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors">
            Get
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   HeroFeatured
   ------------------------------------------------------------------ */

function HeroFeatured({ app, onInstall, installTargets }: { app: CatalogApp; onInstall: (id: string) => void; installTargets: InstallTarget[] }) {
  const [busy, setBusy] = useState(false);
  const [iconFailed, setIconFailed] = useState(false);
  const iconUrl = resolveIconUrl(app);

  const handleGet = async () => {
    if (app.installed) return;
    setBusy(true);
    try {
      const res = await fetch("/api/store/install-v2", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ app_id: app.id, target_remote: installTargets[0]?.name ?? "local" }) });
      if (res.ok) onInstall(app.id);
    } catch { /* ignore */ }
    setBusy(false);
  };

  return (
    <div
      className="relative h-56 rounded-2xl overflow-hidden border border-shell-border-strong flex items-end"
      style={{ background: app.cover ?? "linear-gradient(120deg,#20202a,#14141a)" }}
    >
      {/* Scrim */}
      <div className="absolute inset-0" style={{ background: "linear-gradient(90deg,rgba(10,10,12,0.80) 0%,rgba(10,10,12,0.35) 55%,rgba(10,10,12,0) 100%)" }} />
      <div className="relative p-7" style={{ maxWidth: "62%" }}>
        <div className="text-[11px] font-bold tracking-widest uppercase mb-2" style={{ color: "var(--color-accent)" }}>
          Featured · Editor's Choice
        </div>
        <h2 className="text-[28px] font-extrabold text-shell-text leading-tight tracking-tight">{app.name}</h2>
        <p className="text-[13.5px] text-shell-text-secondary mt-2 leading-relaxed">{app.description}</p>
        <div className="flex items-center gap-3 mt-4">
          <div className="w-12 h-12 rounded-2xl flex items-center justify-center overflow-hidden bg-white/[0.08]" style={{ padding: 8, boxShadow: "0 8px 24px rgba(0,0,0,.4)" }}>
            {iconUrl && !iconFailed
              ? <img src={iconUrl} alt="" className="w-full h-full object-contain" onError={() => setIconFailed(true)} loading="lazy" />
              : <Package className="w-6 h-6 text-white/50" />}
          </div>
          <button type="button" onClick={handleGet} disabled={busy || app.installed} className="px-5 py-2 rounded-full text-[13px] font-bold text-white transition-colors" style={{ background: "var(--color-accent)" }}>
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : app.installed ? "Installed" : "Get"}
          </button>
          <button type="button" className="px-5 py-2 rounded-full text-[13px] font-bold text-shell-text bg-shell-surface-active hover:bg-white/10 transition-colors">
            Preview
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   DiscoverView -- the main landing layout
   ------------------------------------------------------------------ */

function DiscoverView({
  apps, onInstall, installTargets,
}: {
  apps: CatalogApp[];
  onInstall: (id: string) => void;
  installTargets: InstallTarget[];
}) {
  // Hero: first image-gen app with a cover, or comfyui
  const hero = apps.find((a) => a.id === "comfyui") ?? apps.find((a) => a.cover) ?? apps[0];

  // Popular: homelab apps with stars, top 4 by stars
  const popular = [...apps]
    .filter((a) => a.stars !== undefined && a.stars > 0)
    .sort((a, b) => (b.stars ?? 0) - (a.stars ?? 0))
    .slice(0, 6);

  // Subscriptions: curated homelab list
  const subsIds = ["sonarr", "radarr", "qbittorrent", "sabnzbd", "homebridge", "adguard-home", "uptime-kuma", "nextcloud"];
  const subscriptions = subsIds.map((id) => apps.find((a) => a.id === id)).filter(Boolean) as CatalogApp[];

  // Frameworks
  const frameworkApps = apps.filter((a) => a.type === "agent-framework" || a.category === "agent-framework");

  const SectionHeader = ({ title, action }: { title: string; action?: string }) => (
    <div className="flex items-baseline justify-between mb-3">
      <h3 className="text-[18px] font-bold text-shell-text tracking-tight">{title}</h3>
      {action && <button type="button" className="text-[12.5px] text-accent hover:text-shell-text-secondary transition-colors">{action}</button>}
    </div>
  );

  return (
    <div className="flex flex-col gap-7 pb-8">
      {/* Hero */}
      {hero && <HeroFeatured app={hero} onInstall={onInstall} installTargets={installTargets} />}

      {/* Popular this week */}
      {popular.length > 0 && (
        <section>
          <SectionHeader title="Popular this week" action="See all" />
          <div className="flex gap-4 overflow-x-auto pb-2" style={{ scrollbarWidth: "none" }}>
            {popular.map((app) => (
              <RichCard
                key={app.id}
                app={app}
                onInstall={onInstall}
                installTargets={installTargets}
              />
            ))}
          </div>
        </section>
      )}

      {/* Replace your subscriptions */}
      {subscriptions.length > 0 && (
        <section>
          <SectionHeader title="Replace your subscriptions" action="See all" />
          <div className="grid grid-cols-2 gap-x-6 gap-y-1">
            {subscriptions.map((app) => (
              <SubscriptionRow key={app.id} app={app} onInstall={onInstall} installTargets={installTargets} />
            ))}
          </div>
        </section>
      )}

      {/* From the community */}
      <section>
        <SectionHeader title="From the community" action="See all" />
        <div className="flex gap-4 overflow-x-auto pb-2" style={{ scrollbarWidth: "none" }}>
          {COMMUNITY_ITEMS.map((item) => (
            <CommunityCard key={item.id} item={item} />
          ))}
        </div>
      </section>

      {/* Build agents with */}
      {(frameworkApps.length > 0 || FRAMEWORK_ITEMS.length > 0) && (
        <section>
          <SectionHeader title="Build agents with" action="See all" />
          <div className="grid grid-cols-2 gap-x-6 gap-y-1">
            {FRAMEWORK_ITEMS.map((fw) => {
              const catalogApp = apps.find((a) => a.id === fw.id);
              const isInstalled = catalogApp?.installed ?? fw.installed ?? false;
              return (
                <div key={fw.id} className="flex items-center gap-3 px-2.5 py-2.5 rounded-xl hover:bg-shell-surface transition-colors">
                  <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 text-white text-lg font-bold" style={fw.iconStyle}>
                    {fw.iconChar}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13.5px] font-semibold text-shell-text">{fw.name}</div>
                    <div className="text-[11.5px] text-shell-text-tertiary">
                      {fw.sub}{fw.stars ? ` · ★ ${formatStars(fw.stars)}` : ""}
                    </div>
                  </div>
                  {isInstalled
                    ? <span className="flex items-center gap-1 text-[11.5px] text-emerald-400 font-semibold ml-auto"><Check className="w-3 h-3" /> Installed</span>
                    : <button type="button" className="ml-auto px-4 py-1.5 rounded-full bg-shell-surface-active text-shell-text text-[12px] font-bold hover:bg-white/10 transition-colors">Get</button>}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------
   CommunityView -- the community section
   ------------------------------------------------------------------ */

function CommunityView() {
  return (
    <div className="flex flex-col gap-7 pb-8">
      <div>
        <h3 className="text-[18px] font-bold text-shell-text mb-1">From the community</h3>
        <p className="text-[13px] text-shell-text-secondary mb-4">Themes, wallpapers, apps and agents built by taOS users.</p>
        <div className="flex gap-4 overflow-x-auto pb-2" style={{ scrollbarWidth: "none" }}>
          {COMMUNITY_ITEMS.map((item) => (
            <CommunityCard key={item.id} item={item} />
          ))}
        </div>
      </div>
      <div className="p-6 rounded-2xl border border-dashed border-shell-border text-center">
        <Users className="w-8 h-8 text-shell-text-tertiary mx-auto mb-2" />
        <div className="text-[14px] font-semibold text-shell-text mb-1">Share your creation</div>
        <p className="text-[12.5px] text-shell-text-secondary">Build an app or agent with the App Builder and submit it to the community store.</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   StoreApp
   ------------------------------------------------------------------ */

export function StoreApp({ windowId: _windowId }: { windowId: string }) {
  const [apps, setApps] = useState<CatalogApp[]>([]);
  const [search, setSearch] = useState("");
  const [activeNav, setActiveNav] = useState<NavId>("discover");
  const [loading, setLoading] = useState(true);
  const [latest, setLatest] = useState<Record<string, LatestVersion>>({});
  const [agentList, setAgentList] = useState<any[]>([]);
  const [installTargets, setInstallTargets] = useState<InstallTarget[]>([{ name: "local", label: "This controller", type: "local" }]);
  const [runtimeHosts, setRuntimeHosts] = useState<Record<string, string | null>>({});
  const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
  const [selectedBackends, setSelectedBackends] = useState<string[]>([]);
  const [compatMap, setCompatMap] = useState<Map<string, ResolveResponse>>(new Map());

  const userId = typeof window !== "undefined" ? window.localStorage.getItem("taos.user.id") || "anon" : "anon";
  const profileId = typeof window !== "undefined" ? window.localStorage.getItem("taos.profile.id") || "default" : "default";

  const refreshInstalled = useCallback(() => {
    fetch("/api/store/installed-v2", { headers: { Accept: "application/json" } })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        const hosts: Record<string, string | null> = {};
        for (const entry of (data?.installed ?? []) as InstalledEntry[]) hosts[entry.app_id] = entry.runtime_host ?? null;
        setRuntimeHosts(hosts);
      })
      .catch(() => {});
  }, []);

  const fetchCatalog = useCallback(async () => {
    try {
      const res = await fetch("/api/store/catalog", { headers: { Accept: "application/json" } });
      const ct = res.headers.get("content-type") ?? "";
      if (res.ok && ct.includes("application/json")) {
        const data = await res.json();
        if (Array.isArray(data)) {
          const normalized: CatalogApp[] = data.map((a: Record<string, unknown>) => ({
            id: String(a.id),
            name: String(a.name ?? a.id),
            type: String(a.type ?? "plugin"),
            category: a.category ? String(a.category) : undefined,
            version: String(a.version ?? ""),
            description: String(a.description ?? ""),
            installed: Boolean(a.installed),
            compat: (a.compat as CatalogApp["compat"]) ?? "green",
            install_method: a.install_method ? String(a.install_method) : undefined,
            hardware_tiers: (a.hardware_tiers as Record<string, unknown>) ?? undefined,
            variants: (a.variants as CatalogApp["variants"]) ?? undefined,
            repo: a.repo ? String(a.repo) : undefined,
            iconSlug: a.iconSlug ? String(a.iconSlug) : undefined,
            stars: typeof a.stars === "number" ? a.stars : undefined,
            tagline: a.tagline ? String(a.tagline) : undefined,
            cover: a.cover ? String(a.cover) : undefined,
          }));
          // Merge homelab apps: only add those not already in the catalog
          const catalogIds = new Set(normalized.map((a) => a.id));
          const extra = HOMELAB_APPS.filter((a) => !catalogIds.has(a.id));
          setApps([...normalized, ...extra]);
          setLoading(false);
          return true;
        }
      }
    } catch { /* fall through */ }
    return false;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await fetchCatalog();
      if (!ok && !cancelled) { setApps(MOCK_APPS); setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [fetchCatalog]);

  useEffect(() => {
    const modelIds = apps.filter((a) => a.type === "model").map((a) => a.id);
    if (modelIds.length === 0) return;
    let cancelled = false;
    const run = async () => {
      const next = new Map<string, ResolveResponse>();
      for (let i = 0; i < modelIds.length; i += 8) {
        if (cancelled) return;
        const batch = modelIds.slice(i, i + 8);
        const results = await Promise.allSettled(batch.map((id) => resolveModel(id, "auto")));
        results.forEach((r, idx) => {
          const id = batch[idx];
          if (id && r.status === "fulfilled" && r.value && "compat" in r.value) next.set(id, r.value);
        });
        if (!cancelled) setCompatMap(new Map(next));
      }
    };
    run();
    return () => { cancelled = true; };
  }, [apps]);

  useEffect(() => {
    fetchLatestFrameworks().then(setLatest).catch(() => {});
    fetch("/api/agents").then((r) => r.ok ? r.json() : []).then((j) => setAgentList(Array.isArray(j) ? j : (j?.agents ?? []))).catch(() => {});
    fetch("/api/cluster/install-targets", { headers: { Accept: "application/json" } }).then((r) => r.ok ? r.json() : null).then((data) => { if (Array.isArray(data)) setInstallTargets(data); }).catch(() => {});
    refreshInstalled();
  }, [refreshInstalled]);

  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    if (installTargets.length === 0 || apps.length === 0) return;
    const validDevices = installTargets.map((t) => t.name);
    const validBackends = Array.from(new Set(apps.flatMap((a) => (a.variants ?? []).flatMap((v) => v.backend ?? []).concat(a.install_method ? [a.install_method] : []))));
    const persisted = loadFilter(userId, profileId, validDevices, validBackends);
    setSelectedDevices(persisted.devices);
    setSelectedBackends(persisted.backends);
    hydrated.current = true;
  }, [installTargets, apps, userId, profileId]);

  useEffect(() => { saveFilter(userId, profileId, { devices: selectedDevices, backends: selectedBackends }); }, [selectedDevices, selectedBackends, userId, profileId]);

  const handleInstall = useCallback((id: string) => {
    setApps((prev) => prev.map((a) => a.id === id ? { ...a, installed: true } : a));
    refreshInstalled();
    emitAppEvent(APP_INSTALLED, id);
  }, [refreshInstalled]);

  const handleUninstall = useCallback((id: string) => {
    setApps((prev) => prev.map((a) => a.id === id ? { ...a, installed: false } : a));
    refreshInstalled();
  }, [refreshInstalled]);

  // --- Filtering for non-discover views ---
  const NAV_TYPE_MAP: Record<NavId, string[]> = {
    discover: [],
    apps: ["streaming-app", "ai-app", "productivity", "home", "monitoring", "automation", "image-gen", "voice", "video-gen", "plugin"],
    agents: ["agent-framework"],
    models: ["model", "llm-runtime"],
    services: ["service", "infrastructure"],
    mcp: ["mcp"],
    devtools: ["dev-tool"],
    community: [],
    installed: [],
    updates: [],
  };

  const searchFiltered = useMemo(() => {
    if (!search) return apps;
    const q = search.toLowerCase();
    return apps.filter((a) => a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q));
  }, [apps, search]);

  const navFiltered = useMemo(() => {
    if (activeNav === "discover" || activeNav === "community") return searchFiltered;
    if (activeNav === "installed") return searchFiltered.filter((a) => a.installed);
    // Updates lists only installed apps that actually have a newer version
    // available, not every installed app. No update-check feed exists yet, so
    // this is empty until one lands (see the "up to date" empty state below).
    if (activeNav === "updates") return searchFiltered.filter((a) => a.installed && a.update_available === true);
    const types = NAV_TYPE_MAP[activeNav] ?? [];
    if (types.length === 0) return searchFiltered;
    return searchFiltered.filter((a) => types.includes(a.type) || types.includes(a.category ?? ""));
  }, [activeNav, searchFiltered]);

  const selectedDeviceObjs = installTargets.filter((t) => selectedDevices.includes(t.name));
  const tierFilterResult = filterCatalog(navFiltered, selectedDeviceObjs, selectedBackends);

  const filtered: CatalogApp[] = [];
  const incompatible: CatalogApp[] = [...tierFilterResult.incompatible];
  for (const app of tierFilterResult.compatible) {
    if (app.type === "model" && !compatFromResolver(app.id, compatMap, false)) {
      incompatible.push(app);
    } else {
      filtered.push(app);
    }
  }

  const availableBackends = useMemo(() => {
    if (selectedDevices.length === 0) return [];
    const selDevObjs = installTargets.filter((t) => selectedDevices.includes(t.name));
    const tiers = new Set(selDevObjs.map((d) => d.tier_id).filter(Boolean) as string[]);
    const types = NAV_TYPE_MAP[activeNav] ?? [];
    const sourceApps = types.length === 0 ? apps : apps.filter((a) => types.includes(a.type) || types.includes(a.category ?? ""));
    const out = new Set<string>();
    for (const app of sourceApps) {
      if (!app.hardware_tiers) continue;
      const tierMatch = [...tiers].some((t) => app.hardware_tiers![t] !== undefined && app.hardware_tiers![t] !== "unsupported");
      if (!tierMatch) continue;
      for (const v of app.variants ?? []) for (const b of v.backend ?? []) out.add(b);
    }
    return Array.from(out).sort();
  }, [selectedDevices, installTargets, apps, activeNav]);

  useEffect(() => {
    if (availableBackends.length === 0) { if (selectedBackends.length > 0) setSelectedBackends([]); return; }
    const availSet = new Set(availableBackends);
    const dropped = selectedBackends.filter((b) => !availSet.has(b));
    if (dropped.length > 0) setSelectedBackends((prev) => prev.filter((b) => availSet.has(b)));
  }, [availableBackends, selectedBackends]);

  // Hardware profile from first install target
  const primaryTarget = installTargets[0];
  const profileLabel = primaryTarget?.friendly_name ?? primaryTarget?.label ?? "This device";
  const profileSub = primaryTarget?.tier_id ? primaryTarget.tier_id.replace(/-/g, " ") : "Connect a device";

  // When the user is searching, show the results grid even on the curated
  // Discover/Community views (which otherwise ignore the search box).
  const searching = search.trim().length > 0;
  const showGrid = searching || (activeNav !== "discover" && activeNav !== "community");

  return (
    <div className="flex h-full overflow-hidden bg-shell-bg">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 bg-shell-bg-deep border-r border-shell-border flex flex-col overflow-y-auto">
        <div className="px-3 pt-4 pb-2">
          {/* Inline search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-shell-text-tertiary pointer-events-none" />
            <Input
              type="text"
              placeholder="Search apps, agents..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8 h-8 text-xs"
              aria-label="Search apps"
            />
          </div>
        </div>
        <nav className="flex-1 py-1 px-2">
          {(() => {
            let lastGroup: string | undefined = undefined;
            return NAV.map((item) => {
              const showGroupHeader = item.group && item.group !== lastGroup;
              lastGroup = item.group;
              return (
                <div key={item.id}>
                  {showGroupHeader && (
                    <div className="px-3 pt-4 pb-1 text-[10.5px] font-semibold uppercase tracking-widest text-shell-text-tertiary">
                      {item.group}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => setActiveNav(item.id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition-colors ${activeNav === item.id ? "bg-shell-surface-active text-shell-text font-medium" : "text-shell-text-secondary hover:text-shell-text hover:bg-shell-surface"}`}
                    aria-current={activeNav === item.id ? "page" : undefined}
                  >
                    <span className={`shrink-0 ${activeNav === item.id ? "text-accent" : ""}`}>{item.icon}</span>
                    {item.label}
                  </button>
                </div>
              );
            });
          })()}
        </nav>
        {/* Hardware profile pill */}
        <div className="m-3 p-3 rounded-xl bg-shell-surface border border-shell-border">
          <div className="flex items-center gap-1.5 mb-0.5">
            <Cpu className="w-3 h-3 text-accent shrink-0" />
            <span className="text-[12px] font-semibold text-shell-text truncate">{profileLabel}</span>
          </div>
          <div className="text-[11px] text-shell-text-tertiary capitalize">{profileSub}</div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Filters (device + backend) -- shown above all views */}
        <div className="shrink-0 px-6 pt-3 border-b border-shell-border">
          <DevicePillBar
            devices={installTargets}
            selected={selectedDevices}
            onChange={setSelectedDevices}
            showSkeleton={installTargets.length === 0 && loading}
          />
          {hasUnknownHardwareDevice(selectedDeviceObjs) && <UnknownHardwareBanner devices={selectedDeviceObjs} />}
          <BackendPillBar
            available={availableBackends}
            selected={selectedBackends}
            onChange={setSelectedBackends}
            disabled={selectedDevices.length === 0}
          />
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <Loader2 className="w-6 h-6 text-shell-text-tertiary animate-spin" />
            </div>
          ) : activeNav === "community" && !searching ? (
            <CommunityView />
          ) : activeNav === "discover" && !searching ? (
            <DiscoverView apps={apps} onInstall={handleInstall} installTargets={installTargets} />
          ) : showGrid ? (
            <>
              {activeNav === "apps" && !searching && <TaosAppsSection />}
              <div className="mb-4 flex items-baseline justify-between">
                <div>
                  <h2 className="text-[17px] font-bold text-shell-text">{searching ? `Results for "${search.trim()}"` : NAV.find((n) => n.id === activeNav)?.label}</h2>
                  <p className="text-[12px] text-shell-text-tertiary mt-0.5">{filtered.length} apps</p>
                </div>
              </div>
              {searching && filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-shell-text-tertiary text-sm gap-2">
                  <Package className="w-8 h-8" />
                  <span>No matches for &ldquo;{search.trim()}&rdquo;</span>
                </div>
              ) : activeNav === "updates" && filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-shell-text-tertiary text-sm gap-2">
                  <Package className="w-8 h-8" />
                  <span>You&rsquo;re all up to date</span>
                </div>
              ) : activeNav === "installed" && filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-shell-text-tertiary text-sm gap-2">
                  <Package className="w-8 h-8" />
                  <span>Nothing installed yet</span>
                </div>
              ) : activeNav === "models" && filtered.length === 0 ? (
                <div className="p-6 text-center opacity-70 text-shell-text-secondary text-sm">
                  No models cached. <b>taOSmd</b> handles memory by default on every agent.
                </div>
              ) : filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-shell-text-tertiary text-sm gap-2">
                  <Package className="w-8 h-8" />
                  <span>No apps in this category</span>
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
                  {filtered.map((app) => {
                    const latestForApp = latest[app.id];
                    const affected = app.type === "agent-framework"
                      ? agentList.filter((a: any) => a.framework === app.id && a.framework_version_sha && latestForApp && latestForApp.sha !== a.framework_version_sha).length
                      : 0;
                    return (
                      <AppCard
                        key={app.id}
                        app={app}
                        affected={affected}
                        onInstall={handleInstall}
                        onUninstall={handleUninstall}
                        installTargets={installTargets}
                        runtimeHost={runtimeHosts[app.id] ?? null}
                        defaultTargetRemote={selectedDevices.length === 1 ? selectedDevices[0] : undefined}
                        resolveResponse={compatMap.get(app.id)}
                      />
                    );
                  })}
                </div>
              )}
              {incompatible.length > 0 && (
                <IncompatibleToggle count={incompatible.length} compatibleCount={filtered.length}>
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4 mt-2">
                    {incompatible.map((app) => (
                      <AppCard
                        key={app.id}
                        app={app}
                        affected={0}
                        onInstall={handleInstall}
                        onUninstall={handleUninstall}
                        installTargets={installTargets}
                        runtimeHost={runtimeHosts[app.id] ?? null}
                        defaultTargetRemote={selectedDevices.length === 1 ? selectedDevices[0] : undefined}
                        resolveResponse={compatMap.get(app.id)}
                      />
                    ))}
                  </div>
                </IncompatibleToggle>
              )}
            </>
          ) : null}
        </div>
      </main>
    </div>
  );
}

export default StoreApp;
