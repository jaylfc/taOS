// desktop/src/apps/StoreApp/backends.ts

/**
 * Display metadata for each backend that may appear in catalog manifests.
 * BackendPillBar renders pills via this lookup; unknown backends fall
 * back to the raw key with default styling.
 */
export interface BackendMeta {
  /** Human label shown in the pill. */
  label: string;
  /** Single-emoji icon (lightweight; matches existing Store conventions). */
  icon: string;
  /**
   * Tailwind color stem (e.g. "cyan", "blue") — kept for documentation
   * and future programmatic use, but the actual classes for the active
   * pill state must be the literal `classes` string below so Tailwind v4
   * can statically extract them at build time.
   */
  color: string;
  /** Static Tailwind class string for the active (selected) pill state. */
  classes: string;
}

export const BACKEND_META: Record<string, BackendMeta> = {
  rkllama: { label: "rkllama (NPU)", icon: "🧠", color: "cyan", classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" },
  "rk-llama-cpp": { label: "rk-llama.cpp (NPU GGUF)", icon: "🧠", color: "cyan", classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" },
  ezrknpu: { label: "ezrknpu", icon: "🧩", color: "cyan", classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" },
  ollama: { label: "Ollama", icon: "🦙", color: "blue", classes: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  "llama-cpp": { label: "llama.cpp", icon: "🦫", color: "amber", classes: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  vllm: { label: "vLLM", icon: "⚡", color: "yellow", classes: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30" },
  exo: { label: "exo", icon: "🌐", color: "cyan", classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" },
  onnxruntime: { label: "ONNX Runtime", icon: "🟦", color: "blue", classes: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  transformers: { label: "Transformers", icon: "🤗", color: "rose", classes: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  "sentence-transformers": { label: "Sentence Transformers", icon: "🤗", color: "rose", classes: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  diffusers: { label: "Diffusers", icon: "🎨", color: "fuchsia", classes: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30" },
  comfyui: { label: "ComfyUI", icon: "🧩", color: "cyan", classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" },
  "sd-webui": { label: "SD WebUI", icon: "🎨", color: "fuchsia", classes: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30" },
  "stable-diffusion-cpp": { label: "stable-diffusion.cpp", icon: "🖼️", color: "pink", classes: "bg-pink-500/15 text-pink-300 border-pink-500/30" },
  fastsdcpu: { label: "FastSD CPU", icon: "🖌️", color: "teal", classes: "bg-teal-500/15 text-teal-300 border-teal-500/30" },
  "whisper-cpp": { label: "whisper.cpp", icon: "🎙️", color: "sky", classes: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  piper: { label: "Piper", icon: "🗣️", color: "emerald", classes: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  nemo: { label: "NeMo", icon: "🎵", color: "lime", classes: "bg-lime-500/15 text-lime-300 border-lime-500/30" },
};

/** Returns the metadata for `backend`, or a default fallback entry. */
export function backendMeta(backend: string): BackendMeta {
  return (
    BACKEND_META[backend] ?? {
      label: backend,
      icon: "⚙️",
      color: "slate",
      classes: "bg-slate-500/15 text-slate-300 border-slate-500/30",
    }
  );
}
