import { Download, FileCode, Info, Globe } from "lucide-react";
import { exportSiteHtml, downloadSiteHtml } from "./export";
import type { Site } from "./types";

export function ExportView({ site }: { site: Site }) {
  const html = exportSiteHtml(site);
  const sizeKb = (new Blob([html]).size / 1024).toFixed(1);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Export</h2>
        <span className="text-[12px] text-shell-text-tertiary">Self-contained static HTML</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[640px] px-6 py-8">
          <div className="flex items-center gap-3 rounded-xl border border-shell-border bg-shell-surface px-4 py-3.5">
            <FileCode size={20} className="text-accent" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-bold text-shell-text">{site.title}.html</div>
              <div className="text-[11.5px] text-shell-text-tertiary">
                {site.sections.length} sections · {sizeKb} KB · inline CSS, no dependencies
              </div>
            </div>
            <button
              type="button"
              onClick={() => downloadSiteHtml(site)}
              className="flex h-9 items-center gap-2 rounded-[10px] bg-gradient-to-br from-accent to-accent/70 px-4 text-[12.5px] font-bold text-white transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <Download size={15} /> Download .html
            </button>
          </div>

          <p className="mb-2 mt-6 text-[12px] font-semibold text-shell-text-secondary">Preview of the exported HTML</p>
          <pre className="max-h-[300px] overflow-auto rounded-xl border border-shell-border bg-shell-bg-deep p-3.5 text-[11px] leading-relaxed text-shell-text-secondary">
            <code>{html.slice(0, 1400)}{html.length > 1400 ? "\n..." : ""}</code>
          </pre>

          <div className="mt-4 flex items-start gap-2 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3 text-[12px] text-shell-text-secondary" role="status">
            <Info size={15} className="mt-0.5 shrink-0 text-accent" />
            <span>
              Export produces a single portable <code>.html</code> file you can open anywhere or upload to any host.
            </span>
          </div>

          <div className="mt-3 flex items-start gap-2 rounded-xl border border-shell-border bg-shell-surface px-3.5 py-3 text-[12px] text-shell-text-tertiary">
            <Globe size={15} className="mt-0.5 shrink-0" />
            <span>
              To install this site as a real taOS app (or export it as a shareable .taosapp package), save it first,
              then open the Share view.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
