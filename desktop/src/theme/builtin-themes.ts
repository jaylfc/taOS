import type { ThemeConfig } from "./theme-config";

export interface BuiltinTheme {
  theme_id: string;
  name: string;
  builtin: boolean;
  config: ThemeConfig;
}

export const BUILTIN_THEMES: BuiltinTheme[] = [
  {
    theme_id: "default",
    name: "Default",
    builtin: true,
    config: { tokens: {}, structure: {}, effects: [], requires: ["assistant", "launcher"], wallpaper: null },
  },
  {
    theme_id: "matrix-terminal",
    name: "Matrix Terminal",
    builtin: true,
    config: {
      tokens: {
        "--color-shell-bg": "#000800",
        "--color-shell-bg-deep": "#000400",
        "--color-shell-surface": "rgba(0, 255, 70, 0.06)",
        "--color-shell-surface-hover": "rgba(0, 255, 70, 0.10)",
        "--color-shell-surface-active": "rgba(0, 255, 70, 0.14)",
        "--color-shell-border": "rgba(0, 255, 70, 0.18)",
        "--color-shell-border-strong": "rgba(0, 255, 70, 0.35)",
        "--color-shell-text": "#33ff66",
        "--color-shell-text-secondary": "rgba(51, 255, 102, 0.7)",
        "--color-shell-text-tertiary": "rgba(51, 255, 102, 0.45)",
        "--color-accent": "#00ff46",
        "--color-accent-glow": "rgba(0, 255, 70, 0.45)",
        "--color-dock-bg": "rgba(0, 20, 0, 0.92)",
        "--color-topbar-bg": "rgba(0, 20, 0, 0.92)",
        "--font-ui": "'JetBrains Mono', 'SF Mono', ui-monospace, monospace",
        "--font-mono": "'JetBrains Mono', 'SF Mono', ui-monospace, monospace",
      },
      structure: {},
      effects: [{ module: "crt" }, { module: "scanlines" }, { module: "glow" }],
      requires: ["assistant", "launcher"],
      wallpaper: "radial-gradient(ellipse at center, #001a00 0%, #000400 100%)",
    },
  },
];
