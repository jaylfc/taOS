/* ------------------------------------------------------------------ */
/*  Shared constants                                                    */
/* ------------------------------------------------------------------ */

export const MEMORY_STEPS_MB = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072];

export const COLORS = [
  "#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b",
  "#10b981", "#ef4444", "#06b6d4", "#f97316",
];

export const STATUS_STYLES: Record<string, string> = {
  running: "bg-emerald-500/20 text-emerald-400",
  stopped: "bg-zinc-500/20 text-zinc-400",
  error: "bg-red-500/20 text-red-400",
  deploying: "bg-amber-500/20 text-amber-400",
};
