import { useState } from "react";
import { Monitor, Tablet, Smartphone, Eye } from "lucide-react";
import { exportSiteHtml } from "./export";
import { sitePreviewUrl } from "./web-sites-api";
import type { Site, DevicePreview } from "./types";

const DEVICES: { id: DevicePreview; label: string; icon: typeof Monitor; maxW: number | null }[] = [
  { id: "desktop", label: "Desktop", icon: Monitor, maxW: null },
  { id: "tablet", label: "Tablet", icon: Tablet, maxW: 768 },
  { id: "mobile", label: "Mobile", icon: Smartphone, maxW: 380 },
];

/** siteId is the saved site's id (activeId), dirty is true when the in-memory
 *  `site` has unsaved edits, hasRender is true when the saved row carries a
 *  stored index.html the /preview route can serve. Preview is served two ways,
 *  both opaque-origin (`sandbox="allow-scripts"`, no allow-same-origin -- the
 *  one real security upgrade over the old direct-React render):
 *   - saved + clean + hasRender: an iframe pointed at the real, backend-rendered
 *     `/api/web/sites/{id}/preview` (also carries the CSP in routes/web.py).
 *   - otherwise (unsaved, dirty, or a legacy row with no stored render): a
 *     `srcDoc` of a fresh client-side export, so the preview always reflects
 *     the current edits and a legacy row never 404s into a blank/JSON frame.
 *  A site with no sections has nothing to render, so an explicit empty state
 *  is shown instead of a near-blank iframe. */
export function PreviewView({
  site,
  siteId,
  dirty,
  hasRender,
}: {
  site: Site;
  siteId: string | null;
  dirty: boolean;
  hasRender: boolean;
}) {
  const [device, setDevice] = useState<DevicePreview>("desktop");
  const maxW = DEVICES.find((d) => d.id === device)?.maxW ?? undefined;
  const isEmpty = site.sections.length === 0;
  const useLiveUrl = Boolean(siteId) && !dirty && hasRender;

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
                disabled={isEmpty}
                onClick={() => setDevice(d.id)}
                className={`flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-50 ${
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
        {isEmpty ? (
          <div
            className="mx-auto flex h-[70vh] max-w-[520px] flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-shell-border text-center"
            role="status"
          >
            <Eye size={28} className="text-shell-text-tertiary" />
            <p className="text-[13px] font-semibold text-shell-text-secondary">Nothing to preview yet</p>
            <p className="max-w-[320px] text-[12px] text-shell-text-tertiary">
              Add a section in the Edit view, then come back here to see your site.
            </p>
          </div>
        ) : (
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
        )}
      </div>
    </div>
  );
}
