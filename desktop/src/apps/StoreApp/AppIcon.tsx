import { useEffect, useMemo, useState } from "react";
import type { CatalogApp } from "./types";

/* ------------------------------------------------------------------
   AppIcon - one icon surface for the whole Store (desktop + mobile).

   Resolution order:
     1. An explicit dashboard-icons slug (app.iconSlug), or a known
        per-app icon URL (APP_ICONS), or a derived brand family.
     2. A slug derived from the app name, tried against the CDN.
     3. A branded monogram tile: the app's initials on a deterministic
        per-app gradient. Every app gets a clean, intentional icon -
        including the taOS agent frameworks that have no upstream logo.

   The monogram is also the graceful onError target, so a missing or
   rate-limited CDN image never leaves a blank square.
   ------------------------------------------------------------------ */

const di = (slug: string): string =>
  `https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/${slug}.png`;
const gh = (owner: string): string => `https://github.com/${owner}.png?size=96`;

/* Per-app icon overrides for catalog entries that ship without an
   iconSlug. dashboard-icons slugs are verified against the upstream
   repo; GitHub-avatar fallbacks cover orgs the icon set does not carry.
   Anything not listed (and the taOS frameworks) resolves to a monogram. */
const APP_ICONS: Record<string, string> = {
  // Agent frameworks with a real upstream mark
  smolagents: di("hugging-face"),
  "openai-agents-sdk": di("openai"),
  // Models
  "qwen3-4b": di("qwen"), "qwen3-1.7b": di("qwen"), "qwen3-8b": di("qwen"),
  "gemma-3-4b": di("google-gemini"),
  // MCP / plugins
  "github-mcp-server": di("github"), "mcp-memory": di("mcp"),
  "playwright-mcp": gh("microsoft"),
  // Services / dev tools / infra
  searxng: di("searxng"), gitea: di("gitea"), n8n: di("n8n"),
  "code-server": di("coder"), "code-server-kasm": di("coder"),
  blender: di("blender"), libreoffice: di("libreoffice"),
  "jupyter-lab": di("jupyter"), tailscale: di("tailscale"),
  caddy: di("caddy"), animatediff: gh("guoyww"),
  comfyui: di("comfyui"), ollama: di("ollama"),
  "kokoro-tts": di("kokoro-web"), "whisper-stt": di("web-whisper"),
  // Homelab (fallbacks for when the API omits iconSlug)
  "home-assistant": di("home-assistant"), "uptime-kuma": di("uptime-kuma"),
};

/* dashboard-icons family fallbacks, derived from the app id prefix. */
function familyIcon(id: string): string | null {
  if (id.startsWith("qwen")) return di("qwen");
  if (id.startsWith("gemma")) return di("google-gemini");
  if (id.startsWith("llama")) return di("meta");
  if (id.startsWith("phi-")) return gh("microsoft");
  if (id.startsWith("whisper")) return di("web-whisper");
  if (id.startsWith("deepseek")) return di("deepseek");
  if (id.startsWith("mistral") || id.startsWith("mixtral")) return di("mistral-ai");
  if (id.startsWith("flux-")) return di("black-forest-labs");
  return null;
}

/* Derive a dashboard-icons-style slug from a display name as a last
   network attempt before the monogram. "Home Assistant" -> "home-assistant". */
function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/\([^)]*\)/g, " ")       // drop parenthetical notes
    .replace(/[^a-z0-9]+/g, "-")      // punctuation + spaces -> hyphen
    .replace(/^-+|-+$/g, "");
}

/* The first network URL to try for an app, or null to go straight to
   a name-derived slug. */
function primaryIconUrl(app: CatalogApp): string | null {
  if (app.iconSlug) return di(app.iconSlug);
  if (APP_ICONS[app.id]) return APP_ICONS[app.id] ?? null;
  return familyIcon(app.id);
}

/* ------------------------------------------------------------------
   Monogram palette - deterministic per-app gradient.

   Tuned for the macOS-dark graphite shell: each pair is a deep, mid-
   saturation duotone that sits behind a near-white glyph at >= 4.5:1.
   Hues are spread across the wheel (slate, teal, green, amber, copper,
   rose, blue, violet-grey) so neighbouring tiles read as distinct,
   never an AI-purple default.
   ------------------------------------------------------------------ */
const MONOGRAM_GRADIENTS: Array<[string, string]> = [
  ["#3b4a63", "#222b3d"], // slate blue
  ["#1f5d63", "#103138"], // teal
  ["#2f5e44", "#16301f"], // forest green
  ["#6b4a2a", "#2f2113"], // copper
  ["#6a3f4f", "#2e1a22"], // rose
  ["#3a4d7a", "#1b2440"], // indigo grey
  ["#5a5230", "#2a2615"], // olive amber
  ["#4a3a63", "#241b33"], // muted violet
  ["#2c4f6b", "#13283a"], // ocean
  ["#623838", "#2c1717"], // brick
];

function hashName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/* First 1-2 letters: initials of the first two words, or the first two
   characters of a single word. "Agent Zero" -> "AZ", "OpenClaw" -> "OP". */
function monogramText(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return (words[0]![0]! + words[1]![0]!).toUpperCase();
  }
  const w = words[0] ?? "?";
  return w.slice(0, 2).toUpperCase();
}

function Monogram({ app, size, radius }: { app: CatalogApp; size: number; radius: number }) {
  const text = monogramText(app.name);
  const [from, to] = MONOGRAM_GRADIENTS[hashName(app.name) % MONOGRAM_GRADIENTS.length]!;
  return (
    <div
      aria-hidden
      className="flex items-center justify-center w-full h-full"
      style={{
        borderRadius: radius,
        background: `radial-gradient(120% 120% at 30% 22%, ${from}, ${to})`,
      }}
    >
      <span
        style={{
          fontSize: Math.round(size * (text.length > 1 ? 0.4 : 0.5)),
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "rgba(255,255,255,0.94)",
          textShadow: "0 1px 2px rgba(0,0,0,0.35)",
          lineHeight: 1,
          fontFamily:
            "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        }}
      >
        {text}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------
   coverFor - the cover art behind a featured / carousel card.

   Honours an explicit app.cover (the homelab entries set rich, layered
   gradients). For everything else, derives a deterministic two-stop
   radial-plus-linear wash from the SAME hue family as the app's
   monogram, so the icon and its cover read as one identity. Keeps the
   taOS frameworks (OpenClaw, Hermes, ...) on-brand instead of flat.
   ------------------------------------------------------------------ */
export function coverFor(app: CatalogApp): string {
  if (app.cover) return app.cover;
  const [from, to] = MONOGRAM_GRADIENTS[hashName(app.name) % MONOGRAM_GRADIENTS.length]!;
  return (
    `radial-gradient(120% 130% at 18% 16%, ${from}, transparent 58%),` +
    `radial-gradient(120% 130% at 86% 82%, ${to}, transparent 60%),` +
    `linear-gradient(140deg, #20202a, #14141a)`
  );
}

/* ------------------------------------------------------------------
   StoreCover - the cover surface behind a featured / carousel card.

   With app.coverImage: the real photo fills the card (object-cover),
   warmed by a faint top wash and a strong bottom-up dark scrim so the
   icon, name and Get pill overlaid by the caller clear >= 4.5:1.
   Without it (or if the image 404s / is offline): the designed
   gradient from coverFor() shows instead, so a card is never blank.

   The caller positions its own footer/badges absolutely over this; the
   scrim here is purely the legibility layer for that overlaid text.
   ------------------------------------------------------------------ */
export function StoreCover({ app }: { app: CatalogApp }) {
  const [failed, setFailed] = useState(false);
  // A reused instance must retry the new image: clear the failure flag
  // whenever the cover URL changes, so a prior app's load error does not
  // suppress the next app's cover.
  useEffect(() => { setFailed(false); }, [app.coverImage]);
  const gradient = coverFor(app);
  const showImage = !!app.coverImage && !failed;

  return (
    <div className="absolute inset-0" aria-hidden style={{ background: gradient }}>
      {showImage && (
        <img
          src={app.coverImage}
          alt=""
          className="absolute inset-0 w-full h-full object-cover"
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      )}
      {/* Top wash: takes the edge off bright screenshots behind a badge. */}
      <div
        className="absolute inset-x-0 top-0"
        style={{
          height: "42%",
          background: "linear-gradient(180deg,rgba(0,0,0,0.34),transparent)",
        }}
      />
      {/* Bottom-up scrim: the legibility layer for the overlaid footer. */}
      <div
        className="absolute inset-x-0 bottom-0"
        style={{
          height: "78%",
          background:
            "linear-gradient(180deg,transparent 0%,rgba(0,0,0,0.30) 42%,rgba(0,0,0,0.74) 100%)",
        }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------
   AppIcon
   ------------------------------------------------------------------ */

export function AppIcon({
  app,
  size,
  className = "",
}: {
  app: CatalogApp;
  /** Pixel edge length. Hero ~64, carousel ~56, row ~44. */
  size: number;
  className?: string;
}) {
  // Stage 0: explicit/known URL. Stage 1: name-derived CDN slug.
  // Stage 2+: monogram. `stage` advances on each image load error.
  const [stage, setStage] = useState(0);
  const radius = Math.round(size * 0.23);

  const candidates = useMemo<string[]>(() => {
    const urls: string[] = [];
    const primary = primaryIconUrl(app);
    if (primary) urls.push(primary);
    const derived = di(slugFromName(app.name));
    if (!urls.includes(derived)) urls.push(derived);
    return urls;
  }, [app]);

  // A reused instance must start from the first candidate for a new app:
  // reset the resolution stage whenever the candidate URL set changes, so a
  // stale error stage from a prior app does not skip straight to its monogram.
  const candidateKey = candidates.join("|");
  useEffect(() => { setStage(0); }, [candidateKey]);

  const url = candidates[stage];
  const showMonogram = stage >= candidates.length;

  return (
    <div
      className={`relative flex items-center justify-center shrink-0 overflow-hidden ${className}`}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        background: showMonogram ? undefined : "rgba(255,255,255,0.06)",
        boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08)",
      }}
    >
      {showMonogram ? (
        <Monogram app={app} size={size} radius={radius} />
      ) : (
        <img
          key={url}
          src={url}
          alt=""
          className="w-full h-full object-contain"
          style={{ padding: Math.round(size * 0.16) }}
          loading="lazy"
          onError={() => setStage((s) => s + 1)}
        />
      )}
    </div>
  );
}

export default AppIcon;
