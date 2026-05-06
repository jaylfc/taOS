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
  /** Tailwind color stem (e.g. "purple", "blue") used for the pill accent. */
  color: string;
}

export const BACKEND_META: Record<string, BackendMeta> = {
  rkllama: { label: "rkllama (NPU)", icon: "🧠", color: "purple" },
  ollama: { label: "Ollama", icon: "🦙", color: "blue" },
  "llama-cpp": { label: "llama.cpp", icon: "🦫", color: "amber" },
  vllm: { label: "vLLM", icon: "⚡", color: "yellow" },
  transformers: { label: "Transformers", icon: "🤗", color: "rose" },
  diffusers: { label: "Diffusers", icon: "🎨", color: "fuchsia" },
  comfyui: { label: "ComfyUI", icon: "🧩", color: "indigo" },
  "stable-diffusion-cpp": { label: "stable-diffusion.cpp", icon: "🖼️", color: "pink" },
  "rknn-stable-diffusion": { label: "RKNN SD", icon: "🖼️", color: "purple" },
  fastsdcpu: { label: "FastSD CPU", icon: "🖌️", color: "teal" },
  "whisper-cpp": { label: "whisper.cpp", icon: "🎙️", color: "sky" },
  piper: { label: "Piper", icon: "🗣️", color: "emerald" },
  nemo: { label: "NeMo", icon: "🎵", color: "lime" },
};

/** Returns the metadata for `backend`, or a default fallback entry. */
export function backendMeta(backend: string): BackendMeta {
  return (
    BACKEND_META[backend] ?? {
      label: backend,
      icon: "⚙️",
      color: "slate",
    }
  );
}
