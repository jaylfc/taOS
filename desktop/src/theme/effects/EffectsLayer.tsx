import type React from "react";
import { useThemeStore } from "@/stores/theme-store";
import { CrtEffect } from "./CrtEffect";
import { ScanlinesEffect } from "./ScanlinesEffect";
import { GlowEffect } from "./GlowEffect";
import { CursorEffect } from "./CursorEffect";

const REGISTRY: Record<string, React.FC<{ params?: Record<string, unknown> }>> = {
  crt: CrtEffect, scanlines: ScanlinesEffect, glow: GlowEffect, cursor: CursorEffect,
};

export function EffectsLayer() {
  const effects = useThemeStore((s) => s.effects);
  return (
    <div data-testid="effects-layer"
         style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 8000 }}>
      {(effects || []).map((e, i) => {
        const C = REGISTRY[e.module];
        return C ? <div key={i} data-effect={e.module}><C params={e.params} /></div> : null;
      })}
    </div>
  );
}
