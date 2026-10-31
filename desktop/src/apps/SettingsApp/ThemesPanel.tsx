import { useEffect, useState } from "react";
import { Palette, Sparkles, Check } from "lucide-react";
import { Button, Card } from "@/components/ui";
import type { ThemeConfig } from "@/theme/theme-config";
import {
  useThemeStore,
  previewTheme,
  revertPreview,
  keepTheme,
  APP_TYPE_INDICATOR_OPTIONS,
} from "@/stores/theme-store";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { BUILTIN_THEMES } from "@/theme/builtin-themes";

interface InstalledTheme {
  theme_id: string;
  name: string;
  builtin?: boolean;
  config: ThemeConfig;
}

const EMPTY_CONFIG: ThemeConfig = { tokens: {}, structure: {}, effects: [], requires: [] };

export function ThemesPanel() {
  const [themes, setThemes] = useState<InstalledTheme[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [priorConfig, setPriorConfig] = useState<ThemeConfig>(EMPTY_CONFIG);

  const activeThemeId = useThemeStore((s) => s.activeThemeId);
  const appTypeIndicator = useThemeStore((s) => s.appTypeIndicator);
  const setAppTypeIndicator = useThemeStore((s) => s.setAppTypeIndicator);
  const openPanel = useTaosAgentStore((s) => s.openPanel);

  useEffect(() => {
    fetch("/api/themes", { credentials: "include" })
      .then((r) => {
        if (!r.ok) {
          throw new Error(`${r.status} ${r.statusText}`);
        }
        return r.json();
      })
      .then((data: InstalledTheme[]) => {
        const installed = Array.isArray(data) ? data : [];
        const builtinIds = new Set(BUILTIN_THEMES.map((b) => b.theme_id));
        const merged = [
          ...BUILTIN_THEMES,
          ...installed.filter((t) => !builtinIds.has(t.theme_id)),
        ];
        setThemes(merged);
      })
      .catch(() => {
        setError("Could not load installed themes. Showing built-in themes.");
        setThemes([...BUILTIN_THEMES]);
      });
  }, []);

  const handleSelect = (theme: InstalledTheme) => {
    previewTheme(theme.config, priorConfig);
    setPreviewId(theme.theme_id);
  };

  const handleKeep = () => {
    const theme = themes.find((t) => t.theme_id === previewId);
    if (theme) {
      keepTheme(theme.theme_id, theme.config);
      setPriorConfig(theme.config);
    }
    setPreviewId(null);
  };

  const handleRevert = () => {
    revertPreview();
    setPreviewId(null);
  };

  return (
    <section aria-label="Themes">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-5 gap-3">
        <div>
          <h2 className="text-lg font-semibold">Themes</h2>
          <p className="text-sm text-shell-text-tertiary mt-0.5">
            Select a theme to preview it live, then keep or revert.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={openPanel}
          aria-label="Ask the assistant to design a theme"
        >
          <Sparkles size={14} /> Ask the assistant to design a theme
        </Button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {error && (
          <p role="alert" className="text-xs text-amber-400 col-span-full">
            {error}
          </p>
        )}
        {themes.map((theme) => {
          const isActive = theme.theme_id === activeThemeId;
          const isPreviewing = theme.theme_id === previewId;
          return (
            <Card
              key={theme.theme_id}
              role="button"
              tabIndex={0}
              onClick={() => handleSelect(theme)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleSelect(theme);
                }
              }}
              aria-label={`Preview ${theme.name} theme`}
              aria-pressed={isPreviewing}
              className={`p-4 cursor-pointer transition-colors hover:bg-white/[0.06] ${
                isPreviewing ? "ring-2 ring-sky-500" : ""
              }`}
            >
              <div className="flex items-center gap-2 mb-2 text-shell-text-secondary">
                <Palette size={16} />
                {isActive && (
                  <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-medium inline-flex items-center gap-1">
                    <Check size={10} /> Active
                  </span>
                )}
              </div>
              <p className="text-sm font-medium truncate">{theme.name}</p>
            </Card>
          );
        })}
        {themes.length === 0 && (
          <p className="text-sm text-shell-text-tertiary col-span-full">
            No themes installed yet.
          </p>
        )}
      </div>

      <div className="mt-5">
        <h3 className="text-sm font-medium text-shell-text-secondary mb-2">App type indicators</h3>
        <div
          role="radiogroup"
          aria-label="App type indicators"
          className="flex items-center rounded-full border border-shell-border bg-shell-surface p-[3px]"
        >
          {APP_TYPE_INDICATOR_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={appTypeIndicator === option}
              aria-label={option === "badge" ? "Badges" : option === "outline" ? "Outlines" : "Off"}
              onClick={() => setAppTypeIndicator(option)}
              className={[
                "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
                appTypeIndicator === option
                  ? "bg-shell-surface-active text-shell-text"
                  : "text-shell-text-secondary hover:text-shell-text",
              ].join(" ")}
            >
              {option === "badge" ? "Badges" : option === "outline" ? "Outlines" : "Off"}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-shell-text-tertiary">
          Show whether an app is a taOS, Linux, Android, web, container or agent-built app.
        </p>
      </div>

      {previewId && (
        <div
          role="region"
          aria-label="Theme preview controls"
          className="mt-5 flex items-center justify-between gap-3 rounded-lg border border-sky-500/30 bg-sky-500/10 px-4 py-3"
        >
          <span className="text-sm text-sky-200">
            Previewing — keep this theme or revert to your previous one.
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleRevert}>
              Revert
            </Button>
            <Button size="sm" onClick={handleKeep}>
              Keep
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
