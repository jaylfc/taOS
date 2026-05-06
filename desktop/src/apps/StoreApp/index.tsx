import { useState, useEffect, useCallback } from "react";
import { ShoppingBag, Search, Download, Trash2, Check, Package, Loader2, Bot, Brain, Plug, Wrench, Image, Music, Video, Globe, Home, Cpu, Sparkles, Workflow, ClipboardList, Server } from "lucide-react";
import { Button, Card, CardContent, CardFooter, CardHeader, Input } from "@/components/ui";
import { fetchLatestFrameworks, LatestVersion } from "@/lib/framework-api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CatalogApp {
  id: string;
  name: string;
  type: string;
  version: string;
  description: string;
  installed: boolean;
  compat: "green" | "yellow" | "red";
  category?: string;
  install_method?: string;
}

interface InstallTarget {
  name: string;
  label: string;
  type: "local" | "remote";
  addr?: string;
}

interface InstalledEntry {
  app_id: string;
  runtime_host: string | null;
  runtime_port: number | null;
  runtime_backend: string | null;
}

/* ------------------------------------------------------------------ */
/*  Categories                                                         */
/* ------------------------------------------------------------------ */

interface Category {
  id: string;
  label: string;
  icon: React.ReactNode;
  types: string[];       // which app types belong here
  description: string;
}

// Each category lists the identifiers (category OR type fallback) that land in it.
// The Services category is intentionally absent — every service-typed app carries
// an explicit `category:` that routes it to one of the categories below.
const CATEGORIES: Category[] = [
  { id: "all", label: "All Apps", icon: <ShoppingBag size={16} />, types: [], description: "Browse everything" },
  { id: "frameworks", label: "Agent Frameworks", icon: <Bot size={16} />, types: ["agent-framework"], description: "Execution engines for your AI agents" },
  { id: "models", label: "Models", icon: <Brain size={16} />, types: ["model"], description: "Language models for inference" },
  { id: "llm-runtime", label: "LLM Runtime", icon: <Cpu size={16} />, types: ["llm-runtime"], description: "LLM servers, gateways, and distributed inference" },
  { id: "memory", label: "Memory", icon: <Brain size={16} />, types: ["memory"], description: "Memory backends and knowledge stores for agents" },
  { id: "plugins", label: "Plugins", icon: <Plug size={16} />, types: ["plugin"], description: "Tools and capabilities for agents" },
  { id: "mcp-server", label: "MCP Servers", icon: <Cpu size={16} />, types: ["mcp"], description: "Model Context Protocol servers" },
  { id: "ai-app", label: "AI Apps", icon: <Sparkles size={16} />, types: ["ai-app"], description: "Self-hosted AI frontends and builders" },
  { id: "streaming", label: "Streaming Apps", icon: <Globe size={16} />, types: ["streaming-app"], description: "Desktop apps streamed via KasmVNC" },
  { id: "image", label: "Image Generation", icon: <Image size={16} />, types: ["image-gen", "image-model"], description: "Stable Diffusion and image models" },
  { id: "audio", label: "Audio & Voice", icon: <Music size={16} />, types: ["voice", "audio"], description: "TTS and speech-to-text" },
  { id: "music", label: "Music", icon: <Music size={16} />, types: ["music"], description: "Music and sound generation" },
  { id: "video", label: "Video", icon: <Video size={16} />, types: ["video-gen"], description: "Video generation tools" },
  { id: "devtools", label: "Dev Tools", icon: <Wrench size={16} />, types: ["dev-tool"], description: "Development and coding tools" },
  { id: "automation", label: "Automation", icon: <Workflow size={16} />, types: ["automation"], description: "Workflow automation and integrations" },
  { id: "productivity", label: "Productivity", icon: <ClipboardList size={16} />, types: ["productivity"], description: "Notes, files, documents, and collaboration" },
  { id: "home", label: "Home & Monitor", icon: <Home size={16} />, types: ["home", "monitoring"], description: "Home automation and monitoring" },
  { id: "infra", label: "Infrastructure", icon: <Server size={16} />, types: ["infrastructure"], description: "Networking, mail, and system services" },
];

/** The identifier this app groups under — its explicit category, or its type as fallback. */
const appGroup = (app: Pick<CatalogApp, "type" | "category">): string => app.category || app.type;

/* ------------------------------------------------------------------ */
/*  Mock data with proper categories                                   */
/* ------------------------------------------------------------------ */

const MOCK_APPS: CatalogApp[] = [
  // Agent Frameworks
  { id: "smolagents", name: "SmolAgents", type: "agent-framework", version: "1.0.0", description: "HuggingFace code-based agents — well-documented, 26k stars", installed: false, compat: "green" },
  { id: "pocketflow", name: "PocketFlow", type: "agent-framework", version: "1.0.0", description: "Minimal 100-line framework, zero deps, graph-based", installed: false, compat: "green" },
  { id: "openclaw", name: "OpenClaw", type: "agent-framework", version: "1.0.0", description: "Full-featured multi-channel agent framework", installed: true, compat: "green" },
  { id: "langroid", name: "Langroid", type: "agent-framework", version: "1.0.0", description: "Multi-agent message-passing framework", installed: false, compat: "green" },
  { id: "openai-agents-sdk", name: "OpenAI Agents SDK", type: "agent-framework", version: "1.0.0", description: "Provider-agnostic agent SDK from OpenAI", installed: false, compat: "green" },

  // Models
  { id: "qwen3-4b", name: "Qwen3 4B", type: "model", version: "3.0.0", description: "Good balance of speed and capability for most tasks", installed: true, compat: "green" },
  { id: "qwen3-1.7b", name: "Qwen3 1.7B", type: "model", version: "3.0.0", description: "Fast, fits comfortably in 8GB RAM", installed: false, compat: "green" },
  { id: "qwen3-8b", name: "Qwen3 8B", type: "model", version: "3.0.0", description: "Most capable local model for 16GB devices", installed: false, compat: "yellow" },

  // MCP Servers
  { id: "mcp-pandoc", name: "MCP Pandoc", type: "mcp", version: "0.1.0", description: "Document format conversion — markdown, docx, pdf, 30+ formats", installed: false, compat: "green" },
  { id: "mcp-server-office", name: "MCP Office Docs", type: "mcp", version: "0.1.0", description: "Read, write, and edit .docx files programmatically", installed: false, compat: "green" },
  { id: "playwright-mcp", name: "Playwright MCP", type: "mcp", version: "1.0.0", description: "Browser automation for agents via Playwright", installed: false, compat: "green" },
  { id: "github-mcp-server", name: "GitHub MCP", type: "mcp", version: "1.0.0", description: "Issues, PRs, repos, search — official GitHub MCP", installed: false, compat: "green" },
  { id: "mcp-memory", name: "MCP Memory", type: "mcp", version: "1.0.0", description: "Knowledge graph memory for persistent context", installed: false, compat: "green" },
  // Plugins
  { id: "web-search", name: "Web Search", type: "plugin", version: "0.3.0", description: "Search the web via SearXNG or Perplexica", installed: false, compat: "green" },
  { id: "image-generation-tool", name: "Image Generation", type: "plugin", version: "0.1.0", description: "Generate images via Stable Diffusion", installed: false, compat: "green" },

  // Ex-services, now categorised
  { id: "searxng", name: "SearXNG", type: "service", category: "infrastructure", version: "latest", description: "Privacy-respecting metasearch engine", installed: false, compat: "green" },
  { id: "gitea", name: "Gitea", type: "service", category: "dev-tool", version: "latest", description: "Lightweight self-hosted Git service", installed: false, compat: "green" },
  { id: "n8n", name: "n8n", type: "service", category: "automation", version: "latest", description: "Workflow automation platform", installed: false, compat: "green" },

  // Streaming Apps
  { id: "code-server-kasm", name: "Code Server (Streamed)", type: "streaming-app", version: "latest", description: "VS Code in the browser via KasmVNC", installed: false, compat: "green" },
  { id: "blender", name: "Blender", type: "streaming-app", version: "latest", description: "3D creation suite streamed via KasmVNC", installed: false, compat: "yellow" },
  { id: "libreoffice", name: "LibreOffice", type: "streaming-app", version: "latest", description: "Full office suite streamed via KasmVNC", installed: false, compat: "green" },

  // Image Gen
  { id: "comfyui", name: "ComfyUI", type: "image-gen", version: "latest", description: "Node-based Stable Diffusion workflow editor", installed: false, compat: "yellow" },
  { id: "fooocus", name: "Fooocus", type: "image-gen", version: "latest", description: "Simple Stable Diffusion with minimal setup", installed: false, compat: "yellow" },

  // Audio
  { id: "kokoro-tts", name: "Kokoro TTS", type: "voice", version: "latest", description: "High-quality text-to-speech", installed: false, compat: "green" },
  { id: "whisper-stt", name: "Whisper STT", type: "voice", version: "latest", description: "OpenAI Whisper speech-to-text", installed: false, compat: "green" },

  // Video
  { id: "animatediff", name: "AnimateDiff", type: "video-gen", version: "latest", description: "AI video generation from text and images", installed: false, compat: "yellow" },
  { id: "corridorkey", name: "CorridorKey", type: "video-gen", version: "latest", description: "AI video generation via ComfyUI workflows", installed: false, compat: "yellow" },

  // Dev Tools
  { id: "code-server", name: "Code Server", type: "dev-tool", version: "latest", description: "VS Code in the browser — remote development environment", installed: false, compat: "green" },
  { id: "jupyter-lab", name: "JupyterLab", type: "dev-tool", version: "latest", description: "Interactive notebooks for data science and experimentation", installed: false, compat: "green" },

  // Home & Monitor
  { id: "home-assistant", name: "Home Assistant", type: "home", version: "latest", description: "Open-source home automation platform", installed: false, compat: "green" },
  { id: "uptime-kuma", name: "Uptime Kuma", type: "monitoring", version: "latest", description: "Self-hosted monitoring tool — track uptime for services and APIs", installed: false, compat: "green" },

  // Infrastructure
  { id: "tailscale", name: "Tailscale", type: "infrastructure", version: "latest", description: "Zero-config mesh VPN for secure networking between devices", installed: false, compat: "green" },
  { id: "caddy", name: "Caddy", type: "infrastructure", version: "latest", description: "Automatic HTTPS reverse proxy and web server", installed: false, compat: "green" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const TYPE_COLORS: Record<string, string> = {
  "agent-framework": "bg-blue-500/20 text-blue-400",
  model: "bg-slate-500/20 text-slate-400",
  service: "bg-amber-500/20 text-amber-400",
  plugin: "bg-teal-500/20 text-teal-400",
  mcp: "bg-violet-500/20 text-violet-400",
  "streaming-app": "bg-indigo-500/20 text-indigo-400",
  "image-gen": "bg-pink-500/20 text-pink-400",
  "image-model": "bg-pink-500/20 text-pink-400",
  voice: "bg-orange-500/20 text-orange-400",
  audio: "bg-orange-500/20 text-orange-400",
  "video-gen": "bg-red-500/20 text-red-400",
  "dev-tool": "bg-cyan-500/20 text-cyan-400",
  home: "bg-green-500/20 text-green-400",
  monitoring: "bg-green-500/20 text-green-400",
  infrastructure: "bg-slate-500/20 text-slate-400",
  "ai-app": "bg-fuchsia-500/20 text-fuchsia-400",
  automation: "bg-lime-500/20 text-lime-400",
  productivity: "bg-sky-500/20 text-sky-400",
  "llm-runtime": "bg-purple-500/20 text-purple-400",
  music: "bg-rose-500/20 text-rose-400",
};

const TYPE_LABELS: Record<string, string> = {
  "agent-framework": "Framework",
  model: "Model",
  service: "Service",
  plugin: "Plugin",
  mcp: "MCP Server",
  "streaming-app": "Streaming",
  "image-gen": "Image Gen",
  "image-model": "Image Model",
  voice: "Voice",
  audio: "Audio",
  "video-gen": "Video",
  "dev-tool": "Dev Tool",
  home: "Home",
  monitoring: "Monitor",
  infrastructure: "Infra",
  "ai-app": "AI App",
  automation: "Automation",
  productivity: "Productivity",
  "llm-runtime": "LLM Runtime",
  music: "Music",
};

const COMPAT_COLORS: Record<string, string> = { green: "bg-emerald-400", yellow: "bg-amber-400", red: "bg-red-400" };
const COMPAT_LABELS: Record<string, string> = { green: "Compatible", yellow: "Partial", red: "Unsupported" };

const TYPE_ICON_GRADIENTS: Record<string, string> = {
  "agent-framework": "linear-gradient(135deg, rgba(59,130,246,0.3), rgba(59,130,246,0.1))",
  model: "linear-gradient(135deg, rgba(139,92,246,0.3), rgba(139,92,246,0.1))",
  service: "linear-gradient(135deg, rgba(245,158,11,0.3), rgba(245,158,11,0.1))",
  plugin: "linear-gradient(135deg, rgba(20,184,166,0.3), rgba(20,184,166,0.1))",
  "streaming-app": "linear-gradient(135deg, rgba(99,102,241,0.3), rgba(99,102,241,0.1))",
  "image-gen": "linear-gradient(135deg, rgba(236,72,153,0.3), rgba(236,72,153,0.1))",
  voice: "linear-gradient(135deg, rgba(249,115,22,0.3), rgba(249,115,22,0.1))",
  "dev-tool": "linear-gradient(135deg, rgba(6,182,212,0.3), rgba(6,182,212,0.1))",
  "ai-app": "linear-gradient(135deg, rgba(217,70,239,0.3), rgba(217,70,239,0.1))",
  automation: "linear-gradient(135deg, rgba(132,204,22,0.3), rgba(132,204,22,0.1))",
  productivity: "linear-gradient(135deg, rgba(14,165,233,0.3), rgba(14,165,233,0.1))",
  "llm-runtime": "linear-gradient(135deg, rgba(168,85,247,0.3), rgba(168,85,247,0.1))",
  music: "linear-gradient(135deg, rgba(244,63,94,0.3), rgba(244,63,94,0.1))",
};

/* ------------------------------------------------------------------ */
/*  App-specific icons                                                 */
/*                                                                     */
/*  URLs point at Simple Icons (SPDX CC0, curated brand assets) or    */
/*  GitHub org/repo avatars. Every entry is an official logo from     */
/*  the project's own canonical source — no third-party redraws.     */
/*                                                                     */
/*  Loading rules (resolveIconUrl below):                              */
/*  1. Exact id match in APP_ICONS                                    */
/*  2. Derived Simple Icons match for well-known models/services      */
/*  3. Fallback to the Package placeholder icon                       */
/* ------------------------------------------------------------------ */

// Simple Icons CDN returns a white-on-transparent SVG so it blends with
// the dark gunmetal card surface. Colour variants available via
// /{slug}/{hex} but we stick to white for consistency.
const si = (slug: string): string => `https://cdn.simpleicons.org/${slug}/ffffff`;

// GitHub org/user avatar — used for projects without a Simple Icons
// entry. `?size=96` keeps the transfer small; we render at 40px.
const gh = (owner: string): string => `https://github.com/${owner}.png?size=96`;

const APP_ICONS: Record<string, string> = {
  // ---- Agent frameworks ----
  // Where the project ships an official logo in its repo we link it
  // directly; otherwise we use the owning org/user's GitHub avatar.
  "smolagents": gh("huggingface"),
  "pocketflow": gh("The-Pocket"),
  "openclaw": "/static/store-icons/openclaw.jpg",
  "nanoclaw": gh("openclaw"),
  "picoclaw": "https://raw.githubusercontent.com/sipeed/picoclaw/main/assets/logo.webp",
  "zeroclaw": gh("nicholasgasior"),
  "microclaw": gh("nicholasgasior"),
  "ironclaw": gh("nicholasgasior"),
  "nullclaw": gh("nicholasgasior"),
  "shibaclaw": "https://raw.githubusercontent.com/RikyZ90/ShibaClaw/main/assets/shibaclaw_logo.png",
  "moltis": gh("moltis-ai"),
  "hermes": gh("NousResearch"),
  "agent-zero": gh("frdel"),
  "openai-agents-sdk": si("openai"),
  "langroid": gh("langroid"),

  // ---- Model providers (Simple Icons / GitHub) ----
  "qwen2.5-0.5b": gh("QwenLM"), "qwen2.5-1.5b": gh("QwenLM"), "qwen2.5-3b": gh("QwenLM"),
  "qwen2.5-7b": gh("QwenLM"), "qwen2.5-14b": gh("QwenLM"), "qwen2.5-32b": gh("QwenLM"),
  "qwen2.5-72b": gh("QwenLM"), "qwen2.5-1.5b-rkllm": gh("QwenLM"), "qwen2.5-3b-rkllm": gh("QwenLM"),
  "qwen2.5-7b-rkllm": gh("QwenLM"), "qwen2.5-14b-rkllm": gh("QwenLM"),
  "qwen2.5-coder-7b": gh("QwenLM"), "qwen2.5-coder-14b": gh("QwenLM"),
  "qwen2.5-vl-7b": gh("QwenLM"), "qwen2-vl-7b": gh("QwenLM"),
  "qwen3-1.7b": gh("QwenLM"), "qwen3-4b": gh("QwenLM"), "qwen3-8b": gh("QwenLM"),
  "qwen3-14b": gh("QwenLM"), "qwen3-30b-a3b": gh("QwenLM"), "qwen3-32b": gh("QwenLM"),
  "qwen3-embedding-0.6b": gh("QwenLM"), "qwen3-reranker-0.6b": gh("QwenLM"),
  "llama-3.1-8b": si("meta"), "llama-3.2-1b": si("meta"), "llama-3.2-3b": si("meta"),
  "llama-3.3-70b": si("meta"), "llama-3-70b": si("meta"),
  "gemma-2-2b": si("googlegemini"), "gemma-2-9b": si("googlegemini"),
  "gemma-3-1b": si("googlegemini"), "gemma-3-4b": si("googlegemini"), "gemma-3-12b": si("googlegemini"),
  "phi-3.5-mini": gh("microsoft"), "phi-4": gh("microsoft"), "phi-4-mini": gh("microsoft"),
  "mistral-7b-v0.3": gh("mistralai"), "mistral-nemo-12b": gh("mistralai"),
  "mixtral-8x7b": gh("mistralai"), "ministral-3b": gh("mistralai"),
  "deepseek-r1-14b": gh("deepseek-ai"), "deepseek-coder-v2-lite": gh("deepseek-ai"),
  "granite-3.1-2b": gh("ibm-granite"), "granite-3.1-8b": gh("ibm-granite"),
  "command-r-35b": gh("cohere"),
  "smollm2": gh("huggingface"), "smollm2-135m": gh("huggingface"), "smollm2-360m": gh("huggingface"),
  "tinyllama-1.1b": gh("jzhang38"),
  "nemotron-mini-4b": gh("NVIDIA"),
  "pelochus-qwen-1.8b-rkllm": gh("pelochus"),

  // Vision / multimodal
  "llava-1.6-mistral-7b": gh("haotian-liu"), "llava-phi-3-mini": gh("haotian-liu"),
  "minicpm-v-2.6": gh("OpenBMB"),
  "moondream2": gh("vikhyat"),
  "florence-2-base": gh("microsoft"),

  // Embeddings / rerankers
  "bge-large-en-v1.5": gh("FlagOpen"), "bge-small-en-v1.5": gh("FlagOpen"),
  "bge-m3": gh("FlagOpen"), "bge-reranker-v2-m3": gh("FlagOpen"),
  "nomic-embed-text-v1.5": gh("nomic-ai"),
  "mxbai-embed-large": gh("mixedbread-ai"),
  "snowflake-arctic-embed-m": gh("Snowflake-Labs"),

  // Speech
  "whisper-tiny": si("openai"), "whisper-base": si("openai"), "whisper-small": si("openai"),
  "whisper-medium": si("openai"), "whisper-large-v3": si("openai"), "whisper-large-v3-turbo": si("openai"),
  "kokoro-tts": gh("hexgrad"),
  "piper-en-lessac": gh("rhasspy"),
  "parakeet-tdt-0.6b": gh("NVIDIA"),

  // Image models
  "sd-v1.5-lcm": gh("Stability-AI"),
  "dreamshaper-8-lcm": gh("Lykon"),
  "lcm-dreamshaper-v7": gh("Lykon"),
  "sdxl-turbo": gh("Stability-AI"), "sdxl-lightning": gh("ByteDance"),
  "sd3.5-large-turbo-gguf": gh("Stability-AI"),
  "flux-dev-gguf": gh("black-forest-labs"), "flux-schnell-gguf": gh("black-forest-labs"),
  "flux-schnell-unsloth": gh("black-forest-labs"),
  "pixart-sigma-512": gh("PixArt-alpha"),
  "sdxs-512": gh("IDKiro"),
  "playground-v2.5": gh("playgroundai"),
  "kolors": gh("Kwai-Kolors"),
  "auraflow-v0.3": gh("cloneofsimo"),
  "stable-cascade": gh("Stability-AI"),
  "rmbg-1.4": gh("briaai"),
  "birefnet": gh("ZhengPeng7"),
  "real-esrgan-x4": gh("xinntao"),
  "4x-ultrasharp": gh("xinntao"),
  "gfpgan-v1.4": gh("TencentARC"),
  "codeformer": gh("sczhou"),
  "controlnet-canny": gh("lllyasviel"), "controlnet-depth": gh("lllyasviel"),
  "controlnet-openpose": gh("lllyasviel"), "controlnet-openpose-sdxl": gh("lllyasviel"),

  // ---- Services ----
  "comfyui": gh("comfyanonymous"),
  "fooocus": gh("lllyasviel"),
  "stable-diffusion-webui": gh("AUTOMATIC1111"),
  "stable-diffusion-cpp": gh("leejet"),
  "fastsdcpu": gh("rupeshs"),
  "rk-llama-cpp": gh("marty1885"),
  "rk3588-sd-gpu": gh("happyme531"),
  "rknn-stable-diffusion": gh("happyme531"),
  "lcm-dreamshaper-rknn": gh("happyme531"),
  "ltx-video": gh("Lightricks"),
  "wan2gp": gh("alibaba"),
  "musicgpt": gh("gabotechs"),
  "searxng": si("searxng"),
  "gitea": si("gitea"),
  "code-server": gh("coder"),
  "n8n": si("n8n"),
  "home-assistant": si("homeassistant"),
  "uptime-kuma": si("uptimekuma"),
  "filebrowser": gh("filebrowser"),
  "excalidraw": si("excalidraw"),
  "memos": gh("usememos"),
  "linkwarden": gh("linkwarden"),
  "open-webui": gh("open-webui"),
  "dify": gh("langgenius"),
  "perplexica": gh("ItzCrazyKns"),
  "litellm": gh("BerriAI"),
  "stirling-pdf": gh("Stirling-Tools"),
  "paperless-ngx": gh("paperless-ngx"),
  "docling": gh("DS4SD"),
  "libretranslate": gh("LibreTranslate"),
  "mailserver": gh("docker-mailserver"),
  "chatterbox-tts": gh("resemble-ai"),
  "piper-tts": gh("rhasspy"),
  "kokoro-tts-server": gh("remsky"),
  "tailscale": si("tailscale"),
  "ddns": gh("ddclient"),
  "exo": gh("exo-explore"),

  // ---- Plugins / MCP ----
  "github-mcp-server": si("github"),
  "git-mcp": si("git"), "mcp-git": si("git"),
  "mcp-filesystem": gh("modelcontextprotocol"),
  "mcp-fetch": gh("modelcontextprotocol"),
  "mcp-memory": gh("modelcontextprotocol"),
  "mcp-time": gh("modelcontextprotocol"),
  "mcp-sequential-thinking": gh("modelcontextprotocol"),
  "playwright-mcp": si("playwright"),
  "mcp-server-docker": si("docker"),
  "mcp-server-kubernetes": si("kubernetes"),
  "mongodb-mcp-server": si("mongodb"),
  "mcp-redis": si("redis"),
  "chroma-mcp": gh("chroma-core"),
  "supabase-mcp": si("supabase"),
  "dbhub": si("postgresql"),
  "mcp-toolbox-databases": gh("googleapis"),
  "notion-mcp-server": si("notion"),
  "mcp-obsidian": si("obsidian"),
  "mcp-atlassian": si("atlassian"),
  "google-workspace-mcp": si("google"),
  "slack-mcp-server": si("slack"),
  "whatsapp-mcp": si("whatsapp"),
  "ha-mcp": si("homeassistant"),
  "mcp-email-server": gh("modelcontextprotocol"),
  "aws-mcp": si("amazonaws"),
  "cloudflare-mcp": si("cloudflare"),
  "mcp-grafana": si("grafana"),
  "arxiv-mcp-server": si("arxiv"),
  "firecrawl-mcp": gh("mendableai"),
  "exa-mcp-server": gh("exa-labs"),
  "context7-mcp": gh("upstash"),
  "supergateway": gh("supercorp-ai"),
  "browser-use-mcp": gh("browser-use"),
  "camoufox": gh("daijro"),
  "engram": gh("engramhq"),
  "mcp-pandoc": gh("jgeorgeson"),
  "mcp-server-office": gh("GongRzhe"),
  "mcp-server-spreadsheet": gh("GongRzhe"),
  "excel-mcp-server": gh("haris-musa"),
  "markdownify-mcp": gh("zcaceres"),
  "desktop-commander-mcp": gh("wonderwhy-er"),
  "mcpo": gh("open-webui"),
  "youtube-transcript-mcp": si("youtube"),
  "todoist-mcp-server": si("todoist"),
  "playwriter": si("playwright"),
  "image-generation-tool": gh("comfyanonymous"),

  // ---- Streaming apps (legacy MOCK_APPS entries) ----
  "code-server-kasm": gh("coder"),
  "blender": si("blender"),
  "libreoffice": si("libreoffice"),
  "jupyter-lab": si("jupyter"),
  "caddy": gh("caddyserver"),
  "animatediff": gh("guoyww"),
  "corridorkey": gh("comfyanonymous"),
  "whisper-stt": si("openai"),
};

/** Resolve the best icon URL for an app id, falling back through derived matches. */
function resolveIconUrl(appId: string): string | null {
  if (APP_ICONS[appId]) return APP_ICONS[appId];
  // Derived fallbacks for families we haven't enumerated every member of.
  if (appId.startsWith("qwen")) return gh("QwenLM");
  if (appId.startsWith("llama")) return si("meta");
  if (appId.startsWith("gemma")) return si("googlegemini");
  if (appId.startsWith("phi-")) return gh("microsoft");
  if (appId.startsWith("whisper")) return si("openai");
  if (appId.startsWith("deepseek")) return gh("deepseek-ai");
  if (appId.startsWith("mistral") || appId.startsWith("mixtral")) return gh("mistralai");
  if (appId.startsWith("bge-")) return gh("FlagOpen");
  if (appId.startsWith("controlnet")) return gh("lllyasviel");
  if (appId.startsWith("flux-")) return gh("black-forest-labs");
  if (appId.startsWith("sd-") || appId.startsWith("sdxl") || appId.startsWith("sd3")) return gh("Stability-AI");
  return null;
}

/* ------------------------------------------------------------------ */
/*  AppCard                                                            */
/* ------------------------------------------------------------------ */

function AppCard({ app, affected, onInstall, onUninstall, installTargets, runtimeHost }: {
  app: CatalogApp;
  affected: number;
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
  installTargets: InstallTarget[];
  runtimeHost: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [iconFailed, setIconFailed] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState("local");
  const [error, setError] = useState<string | null>(null);
  const iconUrl = resolveIconUrl(app.id);
  const isLxc = app.install_method === "lxc";

  const handleAction = async () => {
    setBusy(true);
    setError(null);
    try {
      if (app.installed) {
        const res = await fetch("/api/store/uninstall", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ app_id: app.id }),
        });
        if (!res.ok) {
          let msg = `Uninstall failed (${res.status})`;
          try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ }
          setError(msg);
          setBusy(false);
          return;
        }
        onUninstall(app.id);
      } else {
        const body: Record<string, unknown> = { app_id: app.id };
        if (isLxc) body.target_remote = selectedTarget;
        const res = await fetch("/api/store/install-v2", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          let msg = `Install failed (${res.status})`;
          try { const err = await res.json(); if (err?.error) msg = String(err.error); } catch { /* ignore */ }
          setError(msg);
          setBusy(false);
          return;
        }
        onInstall(app.id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    }
    setBusy(false);
  };

  return (
    <Card className="flex flex-col rounded-2xl hover:-translate-y-0.5 hover:shadow-2xl hover:border-white/[0.12] transition-all duration-200">
      <CardHeader className="p-5 pb-2">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 overflow-hidden"
              style={{ background: TYPE_ICON_GRADIENTS[appGroup(app)] ?? TYPE_ICON_GRADIENTS[app.type] ?? "rgba(255,255,255,0.06)" }}
            >
              {iconUrl && !iconFailed ? (
                <img
                  src={iconUrl}
                  alt=""
                  className="w-7 h-7 object-contain"
                  onError={() => setIconFailed(true)}
                  loading="lazy"
                />
              ) : (
                <Package className="w-5 h-5 text-white/60" />
              )}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-white/90 truncate text-sm">{app.name}</span>
                {app.installed && <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" />}
                {affected > 0 && (
                  <span className="bg-yellow-700/30 text-yellow-200 text-xs px-2 py-0.5 rounded ml-2 shrink-0">
                    Update available · {affected} {affected === 1 ? "agent" : "agents"}
                  </span>
                )}
              </div>
              <span className="text-[11px] text-white/30">v{app.version}</span>
            </div>
          </div>
          <div className="flex items-center gap-1" title={COMPAT_LABELS[app.compat]}>
            <span className={`w-1.5 h-1.5 rounded-full ${COMPAT_COLORS[app.compat]}`} />
          </div>
        </div>
      </CardHeader>

      <CardContent className="px-5 py-2 flex flex-col gap-3 flex-1">
        <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full w-fit ${TYPE_COLORS[appGroup(app)] ?? TYPE_COLORS[app.type] ?? "bg-white/10 text-white/50"}`}>
          {TYPE_LABELS[appGroup(app)] ?? TYPE_LABELS[app.type] ?? appGroup(app)}
        </span>
        <p className="text-xs text-white/45 leading-relaxed flex-1">{app.description}</p>
      </CardContent>

      <CardFooter className="p-5 pt-2 flex flex-col gap-2 items-stretch">
        {error && (
          <div role="alert" className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
            {error}
          </div>
        )}
        {isLxc && !app.installed && installTargets.length > 1 && (
          <div className="flex items-center gap-2">
            <label htmlFor={`target-${app.id}`} className="text-[11px] text-shell-text-tertiary whitespace-nowrap">
              Install on
            </label>
            <select
              id={`target-${app.id}`}
              value={selectedTarget}
              onChange={(e) => setSelectedTarget(e.target.value)}
              className="flex-1 h-7 rounded-md border border-white/10 bg-shell-bg-deep px-2 text-[11px] text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
              aria-label="Install target host"
            >
              {installTargets.map((t) => (
                <option key={t.name} value={t.name}>{t.label}</option>
              ))}
            </select>
          </div>
        )}
        {app.installed && runtimeHost && (
          <p className="text-[10px] text-shell-text-tertiary flex items-center gap-1">
            <Server className="w-3 h-3 shrink-0" />
            {runtimeHost === "127.0.0.1" ? "on controller" : `on ${runtimeHost}`}
          </p>
        )}
        <Button
          variant={app.installed ? "destructive" : "default"}
          size="sm"
          className="w-full"
          onClick={handleAction}
          disabled={busy}
          aria-label={app.installed ? `Uninstall ${app.name}` : `Install ${app.name}`}
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : app.installed ? <><Trash2 className="w-3.5 h-3.5" /> Uninstall</> : <><Download className="w-3.5 h-3.5" /> Install</>}
        </Button>
      </CardFooter>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  StoreApp                                                           */
/* ------------------------------------------------------------------ */

export function StoreApp({ windowId: _windowId }: { windowId: string }) {
  const [apps, setApps] = useState<CatalogApp[]>([]);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("all");
  const [loading, setLoading] = useState(true);
  const [latest, setLatest] = useState<Record<string, LatestVersion>>({});
  const [agentList, setAgentList] = useState<any[]>([]);
  const [installTargets, setInstallTargets] = useState<InstallTarget[]>([
    { name: "local", label: "This controller", type: "local" },
  ]);
  const [runtimeHosts, setRuntimeHosts] = useState<Record<string, string | null>>({});

  const refreshInstalled = useCallback(() => {
    fetch("/api/store/installed-v2", { headers: { Accept: "application/json" } })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        const hosts: Record<string, string | null> = {};
        for (const entry of (data?.installed ?? []) as InstalledEntry[]) {
          hosts[entry.app_id] = entry.runtime_host ?? null;
        }
        setRuntimeHosts(hosts);
      })
      .catch(() => {});
  }, []);

  const fetchCatalog = useCallback(async () => {
    try {
      const res = await fetch("/api/store/catalog", {
        headers: { Accept: "application/json" },
      });
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
          }));
          setApps(normalized);
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
    const qs = new URLSearchParams(window.location.hash.split("?")[1] || "");
    const cat = qs.get("category");
    if (cat) setActiveCategory(cat);
  }, []);

  useEffect(() => {
    fetchLatestFrameworks().then(setLatest).catch(() => {});
    fetch("/api/agents")
      .then((r) => r.ok ? r.json() : [])
      .then((j) => setAgentList(Array.isArray(j) ? j : (j?.agents ?? [])))
      .catch(() => {});
    fetch("/api/cluster/install-targets", { headers: { Accept: "application/json" } })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (Array.isArray(data)) setInstallTargets(data); })
      .catch(() => {});
    refreshInstalled();
  }, [refreshInstalled]);

  const activeCat = CATEGORIES.find((c) => c.id === activeCategory);

  const filtered = apps.filter((app) => {
    if (activeCategory !== "all" && activeCat) {
      if (!activeCat.types.includes(appGroup(app))) return false;
    }
    if (search) {
      const q = search.toLowerCase();
      return app.name.toLowerCase().includes(q) || app.description.toLowerCase().includes(q);
    }
    return true;
  });

  const handleInstall = useCallback((id: string) => {
    setApps((prev) => prev.map((a) => (a.id === id ? { ...a, installed: true } : a)));
    refreshInstalled();
  }, [refreshInstalled]);

  const handleUninstall = useCallback((id: string) => {
    setApps((prev) => prev.map((a) => (a.id === id ? { ...a, installed: false } : a)));
    refreshInstalled();
  }, [refreshInstalled]);

  // Count per category
  const counts: Record<string, number> = {};
  for (const cat of CATEGORIES) {
    if (cat.id === "all") { counts[cat.id] = apps.length; continue; }
    counts[cat.id] = apps.filter((a) => cat.types.includes(appGroup(a))).length;
  }

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  return (
    <div className={`flex ${isMobile ? "flex-col" : ""} h-full overflow-hidden`}>
      {/* Sidebar / Mobile pill row */}
      {isMobile ? (
        <div className="flex overflow-x-auto gap-2 px-3 py-2 border-b border-shell-border shrink-0">
          {CATEGORIES.map((cat) => (
            <Button
              key={cat.id}
              variant="outline"
              size="sm"
              onClick={() => setActiveCategory(cat.id)}
              className={`whitespace-nowrap rounded-full ${
                activeCategory === cat.id ? "bg-accent/15 text-accent border-accent/30" : ""
              }`}
            >
              {cat.label}
            </Button>
          ))}
        </div>
      ) : (
        <div className="w-52 shrink-0 border-r border-shell-border bg-shell-surface/30 flex flex-col overflow-y-auto">
          <div className="px-3 py-3 border-b border-shell-border">
            <div className="flex items-center gap-2">
              <ShoppingBag size={16} className="text-accent" />
              <span className="text-sm font-medium text-shell-text">Store</span>
            </div>
          </div>
          <nav className="flex-1 py-2 px-2 space-y-0.5">
            {CATEGORIES.map((cat) => (
              <Button
                key={cat.id}
                variant="ghost"
                size="sm"
                onClick={() => setActiveCategory(cat.id)}
                className={`w-full justify-start gap-2.5 text-xs ${
                  activeCategory === cat.id ? "bg-accent/15 text-accent hover:bg-accent/20 hover:text-accent" : ""
                }`}
              >
                <span className="shrink-0">{cat.icon}</span>
                <span className="flex-1 truncate text-left">{cat.label}</span>
                {counts[cat.id] ? (
                  <span className="text-[10px] text-shell-text-tertiary">{counts[cat.id]}</span>
                ) : null}
              </Button>
            ))}
          </nav>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <header className="shrink-0 px-5 py-4 border-b border-shell-border">
          <div className="flex items-center justify-between mb-1">
            <div>
              <h2 className="text-base font-medium text-shell-text">{activeCat?.label ?? "All Apps"}</h2>
              <p className="text-xs text-shell-text-tertiary">{activeCat?.description}</p>
            </div>
            <span className="text-xs text-shell-text-tertiary">{filtered.length} apps</span>
          </div>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-shell-text-tertiary pointer-events-none z-10" />
            <Input
              type="text"
              placeholder="Search..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
              aria-label="Search apps"
            />
          </div>
        </header>

        {/* Grid */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <Loader2 className="w-6 h-6 text-shell-text-tertiary animate-spin" />
            </div>
          ) : filtered.length === 0 && activeCategory === "memory" ? (
            <div className="p-6 text-center opacity-70">
              No third-party memory plugins yet. <b>taOSmd</b> is installed by default and available on every agent.
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-shell-text-tertiary text-sm gap-2">
              <Package className="w-8 h-8" />
              <span>No apps in this category</span>
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4">
              {filtered.map((app) => {
                const latestForApp = latest[app.id];
                const affected = app.type === "agent-framework"
                  ? agentList.filter(
                      (a: any) =>
                        a.framework === app.id &&
                        a.framework_version_sha &&
                        latestForApp &&
                        latestForApp.sha !== a.framework_version_sha
                    ).length
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
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default StoreApp;
