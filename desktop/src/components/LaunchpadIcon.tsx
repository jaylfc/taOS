import * as icons from "lucide-react";
import { prefetchApp, type AppManifest } from "@/registry/app-registry";

interface Props {
  app: AppManifest;
  onClick: () => void;
}

export function LaunchpadIcon({ app, onClick }: Props) {
  const iconName = app.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  const IconComponent = (icons[iconName] as icons.LucideIcon) ?? icons.HelpCircle;

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => prefetchApp(app.id)}
      onPointerDown={() => prefetchApp(app.id)}
      className="flex flex-col items-center gap-2 p-3 rounded-xl hover:bg-white/5 transition-colors"
      aria-label={`Open ${app.name}`}
    >
      <div className="w-14 h-14 rounded-2xl bg-shell-surface-hover flex items-center justify-center">
        <IconComponent size={28} className="text-shell-text" />
      </div>
      <span className="text-xs text-shell-text-secondary">{app.name}</span>
    </button>
  );
}
