import { useCallback, useState } from "react";
import * as icons from "lucide-react";
import { Check, Download, Loader2, Trash2 } from "lucide-react";
import { OPTIONAL_APPS, type OptionalAppMeta } from "@/registry/optional-apps";
import { useInstalledOptionalApps } from "@/hooks/use-installed-optional-apps";
import { emitAppEvent, APP_OPTIONAL_CHANGED } from "@/lib/app-event-bus";

/**
 * "taOS Apps" — the optional, frontend-only apps (Reddit / YouTube / GitHub /
 * X). Unlike service catalog apps these install instantly with no device
 * target or progress: a single POST flips server-side install state, then we
 * emit APP_OPTIONAL_CHANGED so the launchpad surfaces or hides the app at once.
 */
export function TaosAppsSection() {
  const installed = useInstalledOptionalApps();

  return (
    <section className="mb-7" aria-labelledby="taos-apps-heading">
      <div className="mb-3">
        <h3 id="taos-apps-heading" className="text-[15px] font-bold text-shell-text">
          taOS Apps
        </h3>
        <p className="text-[12px] text-shell-text-tertiary mt-0.5">
          Optional first-party apps. Install the ones you want; remove them any time.
        </p>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {OPTIONAL_APPS.map((meta) => (
          <OptionalAppCard key={meta.id} meta={meta} installed={installed.has(meta.id)} />
        ))}
      </div>
    </section>
  );
}

function OptionalAppCard({ meta, installed }: { meta: OptionalAppMeta; installed: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // lucide icon names are PascalCase exports; the registry stores kebab-case.
  const pascal = meta.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
  const Glyph = (icons[pascal as keyof typeof icons] as icons.LucideIcon) ?? icons.Package;

  const toggle = useCallback(async () => {
    setBusy(true);
    setError(null);
    const verb = installed ? "uninstall" : "install";
    try {
      const res = await fetch(`/api/apps/optional/${encodeURIComponent(meta.id)}/${verb}`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`${verb} failed (${res.status})`);
      emitAppEvent(APP_OPTIONAL_CHANGED, meta.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }, [installed, meta.id]);

  return (
    <div className="flex flex-col rounded-2xl border border-shell-border bg-shell-surface/60 overflow-hidden">
      <div
        className="h-20 relative shrink-0 flex items-center justify-center"
        style={{ background: meta.cover }}
      >
        <Glyph size={28} className="text-white/90" />
        {installed && (
          <span className="absolute top-2 right-2 flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-black/35 backdrop-blur-sm text-white/90">
            <Check className="w-3 h-3" /> Installed
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2 p-3 flex-1">
        <span className="text-[13px] font-semibold text-shell-text leading-snug">{meta.name}</span>
        <p className="text-[11.5px] text-shell-text-secondary leading-relaxed flex-1">{meta.tagline}</p>
        {error && (
          <div role="alert" className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
            {error}
          </div>
        )}
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          className={[
            "flex items-center justify-center gap-1.5 h-8 rounded-lg text-[12px] font-semibold transition-colors disabled:opacity-60",
            installed
              ? "border border-shell-border text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.04]"
              : "bg-accent text-white hover:bg-accent/90",
          ].join(" ")}
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : installed ? (
            <><Trash2 className="w-3.5 h-3.5" /> Remove</>
          ) : (
            <><Download className="w-3.5 h-3.5" /> Install</>
          )}
        </button>
      </div>
    </div>
  );
}
