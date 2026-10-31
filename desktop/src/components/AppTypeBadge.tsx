import { useThemeStore } from "@/stores/theme-store";
import { Box, Globe, Smartphone, Sparkles, Terminal } from "lucide-react";

type Runtime = "taos" | "linux" | "android" | "web" | "container" | "agent";

const GLYPH: Record<Exclude<Runtime, "taos">, typeof Terminal> = {
  linux: Terminal,
  android: Smartphone,
  web: Globe,
  container: Box,
  agent: Sparkles,
};

const LABEL: Record<Exclude<Runtime, "taos">, string> = {
  linux: "Linux app",
  android: "Android app",
  web: "Web app",
  container: "Container app",
  agent: "Agent app",
};

const SIZE_PX: Record<"sm" | "md" | "lg", number> = {
  sm: 12,
  md: 16,
  lg: 20,
};

export interface AppTypeBadgeProps {
  runtime: Runtime;
  /** sm=12px, md=16px, lg=20px chip. */
  size?: "sm" | "md" | "lg";
}

/**
 * Corner indicator showing what TYPE of app an icon is. Renders nothing for a
 * taOS app or when the user has turned indicators off, so the badge is opt-in
 * per-app and per-user. The parent must be `relative` (the chip is absolute).
 */
export function AppTypeBadge({ runtime, size = "md" }: AppTypeBadgeProps) {
  const appTypeIndicator = useThemeStore((s) => s.appTypeIndicator);
  if (runtime === "taos" || appTypeIndicator === "off") return null;

  const Glyph = GLYPH[runtime];
  const label = LABEL[runtime];
  const px = SIZE_PX[size];
  const token = `--app-type-${runtime}` as const;

  if (appTypeIndicator === "outline") {
    return (
      <span
        role="img"
        aria-label={label}
        className="pointer-events-none absolute inset-0 rounded-[inherit] border-2"
        style={{ borderColor: `var(${token})` }}
      >
        <span className="sr-only">{label}</span>
      </span>
    );
  }

  return (
    <span
      role="img"
      aria-label={label}
      className="absolute bottom-0 right-0 flex items-center justify-center rounded-full"
      style={{ width: px, height: px, backgroundColor: `var(${token})` }}
    >
      <Glyph size={px} className="text-shell-bg" aria-hidden />
      <span className="sr-only">{label}</span>
    </span>
  );
}