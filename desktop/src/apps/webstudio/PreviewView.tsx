import { useState } from "react";
import { Monitor, Tablet, Smartphone } from "lucide-react";
import { exportSiteHtml } from "./export";
import { sitePreviewUrl } from "./web-sites-api";
import type { Site, DevicePreview } from "./types";

const DEVICES: { id: DevicePreview; label: string; icon: typeof Monitor; maxW: number | null }[] = [
  { id: "desktop", label: "Desktop", icon: Monitor, maxW: null },
  { id: "tablet", label: "Tablet", icon: Tablet, maxW: 768 },
  { id: "mobile", label: "Mobile", icon: Smartphone, maxW: 380 },
];

/** siteId is the saved site's id (activeId), dirty is true when the in-memory
 *  `site` has unsaved edits. Preview is served two ways, both opaque-origin
 *  (`sandbox="allow-scripts"`, no allow-same-origin -- the one real security
 *  upgrade over the old direct-React render):
 *   - saved + clean: an iframe pointed at the real, backend-rendered
 *     `/api/web/sites/{id}/preview` (also carries the CSP in routes/web.py).
 *   - unsaved or never-saved: `srcDoc` of a fresh client-side export, so the
 *     preview always reflects the current edits without a round-trip. */
export function PreviewView({ site, siteId, dirty }: { site: Site; siteId: string | null; dirty: boolean }) {
  const [device, setDevice] = useState<DevicePreview>("desktop");
  const maxW = DEVICES.find((d) => d.id === device)?.maxW ?? undefined;
  const useLiveUrl = Boolean(siteId) && !dirty;

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
          {useLiveUrl ? (
            <iframe
              key={siteId}
              title="Site preview"
              src={sitePreviewUrl(siteId!)}
              sandbox="allow-scripts"
              className="h-[70vh] w-full border-0 bg-white"
            />
          ) : (
            <iframe
              key="unsaved-preview"
              title="Site preview (unsaved)"
              srcDoc={exportSiteHtml(site)}
              sandbox="allow-scripts"
              className="h-[70vh] w-full border-0 bg-white"
            />
          )}
        </div>
      </div>
    </div>
  );
}
