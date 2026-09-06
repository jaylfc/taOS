import { useCallback, useEffect, useState } from "react";
import { Wand2, LayoutGrid, Pencil, Eye, Download, Share2 } from "lucide-react";
import { GenerateView } from "./webstudio/GenerateView";
import { TemplatesView } from "./webstudio/TemplatesView";
import { EditView, type SavedSite } from "./webstudio/EditView";
import { PreviewView } from "./webstudio/PreviewView";
import { ExportView } from "./webstudio/ExportView";
import { ShareView } from "./webstudio/ShareView";
import { exportSiteHtml } from "./webstudio/export";
import { emptySite } from "./webstudio/templates";
import { isValidSite, MAX_CONTENT_BYTES, type Site, type StudioView } from "./webstudio/types";

/* ------------------------------------------------------------------ */
/*  Web Studio - shell                                                  */
/*                                                                     */
/*  A Wix-style, section-based website builder. Left icon rail         */
/*  (Generate / Templates / Edit / Preview / Export / Share) + the      */
/*  active surface, the same shape as the other taOS studios.           */
/*                                                                     */
/*  Generate streams the taos-agent for a real, bespoke Site (with an   */
/*  honest template-match fallback if that fails); Edit is a visual     */
/*  sections editor; Preview is a sandboxed iframe (either the saved,    */
/*  backend-rendered page or a srcDoc of the live in-memory edits);      */
/*  Export downloads the self-contained static HTML; Share installs the  */
/*  site as a real taOS app (or exports the same .taosapp package).      */
/*  Saving also persists the rendered index.html alongside the editable  */
/*  Site JSON, so Preview/Share have something servable.                 */
/* ------------------------------------------------------------------ */

const RAIL: { id: StudioView; label: string; icon: typeof Wand2 }[] = [
  { id: "generate", label: "Generate", icon: Wand2 },
  { id: "templates", label: "Templates", icon: LayoutGrid },
  { id: "edit", label: "Edit", icon: Pencil },
  { id: "preview", label: "Preview", icon: Eye },
  { id: "export", label: "Export", icon: Download },
  { id: "share", label: "Share", icon: Share2 },
];

export function WebStudioApp(_props: { windowId: string }) {
  const [view, setView] = useState<StudioView>("generate");
  const [site, setSite] = useState<Site>(() => emptySite());
  const [saved, setSaved] = useState<SavedSite[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  // True when the active saved site has a stored rendered index.html the
  // /preview route can serve. Legacy rows (saved before this feature) have an
  // empty index_html, so Preview must fall back to a client-side srcDoc render
  // rather than point the iframe at a 404. Any save with the current code
  // populates it, so it's true after a successful save.
  const [activeHasRender, setActiveHasRender] = useState(false);
  // Honest provenance for the current editing session: only a site the agent
  // actually generated is "ai-generated"; templates, manual edits, a matched
  // fallback, or a reopened saved site are "user-uploaded" (the safe default;
  // both tiers carry the same capability ceiling, so this is a labeling fix).
  const [provenance, setProvenance] = useState<"ai-generated" | "user-uploaded">("user-uploaded");

  const loadList = useCallback(async () => {
    const res = await fetch("/api/web/sites", { credentials: "include" });
    if (!res.ok) throw new Error("Could not load sites");
    setSaved((await res.json()) as SavedSite[]);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await loadList();
        if (!cancelled) setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadList]);

  /** True if the user should be prompted before discarding in-memory edits. */
  const confirmDiscard = () =>
    !dirty || window.confirm("Discard unsaved changes to the current site?");

  const seedInEditor = (next: Site, wasAiGenerated = false) => {
    if (!confirmDiscard()) return;
    setSite(next);
    setActiveId(null);
    setView("edit");
    setDirty(false);
    setActiveHasRender(false);
    setProvenance(wasAiGenerated ? "ai-generated" : "user-uploaded");
  };

  const newSite = () => {
    if (!confirmDiscard()) return;
    setSite(emptySite());
    setActiveId(null);
    setError(null);
    setDirty(false);
    setActiveHasRender(false);
    setProvenance("user-uploaded");
  };

  const updateSite = (next: Site) => {
    setSite(next);
    setDirty(true);
  };

  const openSite = async (id: string) => {
    if (!confirmDiscard()) return;
    setError(null);
    try {
      const res = await fetch(`/api/web/sites/${encodeURIComponent(id)}`, { credentials: "include" });
      if (!res.ok) throw new Error("Could not open site");
      const row = (await res.json()) as { id: string; title: string; content: string; index_html?: string };
      let model: Site;
      try {
        const parsed: unknown = JSON.parse(row.content);
        if (!isValidSite(parsed)) throw new Error("malformed site data");
        model = parsed;
      } catch {
        setError("This site's saved data is corrupted; opened a blank site instead.");
        model = emptySite();
      }
      setSite(model);
      setActiveId(row.id);
      setDirty(false);
      // Legacy rows have no stored render; Preview will use a srcDoc fallback.
      setActiveHasRender(Boolean(row.index_html));
      // A reopened saved site's origin isn't tracked across sessions; fall
      // back to the safe, non-agent tier rather than assume ai-generated.
      setProvenance("user-uploaded");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Open failed");
    }
  };

  const saveSite = async () => {
    setSaving(true);
    setError(null);
    try {
      const content = JSON.stringify(site);
      // The rendered static HTML persisted alongside the editable Site JSON --
      // the derived artifact the preview/package routes serve, so Preview and
      // Share never have to re-render the site server-side.
      const indexHtml = exportSiteHtml(site);
      // Catch an over-cap site (usually too many/too-large inlined images)
      // here with a clear message rather than letting it fail only at PUT time
      // with a raw 413. Both the JSON content and the rendered HTML are capped
      // at the backend's MAX_CONTENT_BYTES, so check both before the request.
      if (
        new Blob([content]).size > MAX_CONTENT_BYTES ||
        new Blob([indexHtml]).size > MAX_CONTENT_BYTES
      ) {
        throw new Error(
          "This site is too large to save (over 5 MB). Remove or shrink some images and try again.",
        );
      }
      const payload = { title: site.title.trim() || "Untitled site", content, index_html: indexHtml };
      const url = activeId ? `/api/web/sites/${encodeURIComponent(activeId)}` : "/api/web/sites";
      const res = await fetch(url, {
        method: activeId ? "PUT" : "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error || "Save failed");
      }
      const savedRow = (await res.json()) as { id: string };
      setActiveId(savedRow.id);
      setDirty(false);
      // We just persisted index_html, so the /preview route can serve it.
      setActiveHasRender(true);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const deleteSite = async (id: string) => {
    setError(null);
    try {
      const res = await fetch(`/api/web/sites/${encodeURIComponent(id)}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!res.ok) throw new Error("Delete failed");
      if (activeId === id) setActiveId(null);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Web Studio views"
          className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5"
        >
          {RAIL.map((r) => {
            const Icon = r.icon;
            const on = view === r.id;
            return (
              <button
                key={r.id}
                type="button"
                aria-label={r.label}
                aria-current={on ? "page" : undefined}
                onClick={() => setView(r.id)}
                className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  on
                    ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                    : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
                }`}
              >
                <Icon size={21} />
                {r.label}
              </button>
            );
          })}
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          {view === "generate" && <GenerateView onGenerate={seedInEditor} />}
          {view === "templates" && <TemplatesView onLoad={seedInEditor} />}
          {view === "edit" && (
            <EditView
              site={site}
              onChange={updateSite}
              saved={saved}
              activeId={activeId}
              loading={loading}
              saving={saving}
              error={error}
              onNew={newSite}
              onOpen={openSite}
              onSave={saveSite}
              onDelete={deleteSite}
            />
          )}
          {view === "preview" && (
            <PreviewView site={site} siteId={activeId} dirty={dirty} hasRender={activeHasRender} />
          )}
          {view === "export" && <ExportView site={site} />}
          {view === "share" && <ShareView siteId={activeId} provenance={provenance} />}
        </div>
      </div>
    </div>
  );
}
