import { useState } from "react";
import { Monitor, Tablet, Smartphone } from "lucide-react";
import { SectionBlock } from "./SectionBlock";
import { PALETTES, FONTS, type Site, type DevicePreview } from "./types";

const DEVICES: { id: DevicePreview; label: string; icon: typeof Monitor; maxW: number | null }[] = [
  { id: "desktop", label: "Desktop", icon: Monitor, maxW: null },
  { id: "tablet", label: "Tablet", icon: Tablet, maxW: 768 },
  { id: "mobile", label: "Mobile", icon: Smartphone, maxW: 380 },
];

export function PreviewView({ site }: { site: Site }) {
  const [device, setDevice] = useState<DevicePreview>("desktop");
  const colors = PALETTES[site.theme.palette].colors;
  const fontStack = FONTS[site.theme.font].stack;
  const maxW = DEVICES.find((d) => d.id === device)?.maxW ?? undefined;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Preview</h2>
        <div className="ml-auto flex items-center gap-1 rounded-lg border border-shell-border bg-shell-surface p-0.5" role="group" aria-label="Device preview">
          {DEVICES.map((d) => {
            const Icon = d.icon;
            const on = device === d.id;
            return (
              <button
                key={d.id}
                type="button"
                aria-label={d.label}
                aria-pressed={on}
                onClick={() => setDevice(d.id)}
                className={`flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  on ? "bg-shell-surface-active text-shell-text" : "text-shell-text-tertiary hover:text-shell-text-secondary"
                }`}
              >
                <Icon size={14} /> {d.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-shell-bg-deep p-6">
        <div
          className="mx-auto overflow-hidden rounded-xl border border-shell-border transition-all"
          style={{ maxWidth: maxW }}
        >
          {site.sections.map((s) => (
            <SectionBlock key={s.id} section={s} colors={colors} fontStack={fontStack} editable={false} />
          ))}
        </div>
      </div>
    </div>
  );
}
