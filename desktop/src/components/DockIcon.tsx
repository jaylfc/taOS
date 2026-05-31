import * as icons from "lucide-react";
import { getApp, prefetchApp } from "@/registry/app-registry";

interface Props {
  appId: string;
  isRunning: boolean;
  onClick: () => void;
}

export function DockIcon({ appId, isRunning, onClick }: Props) {
  const app = getApp(appId);
  if (!app) return null;

  const iconName = app.icon
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  const IconComponent = (icons[iconName] as icons.LucideIcon) ?? icons.HelpCircle;

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => prefetchApp(appId)}
      className="group relative flex items-center justify-center w-10 h-10 rounded-lg bg-shell-surface hover:bg-shell-surface-active transition-all hover:scale-110"
      aria-label={`Open ${app.name}`}
      title={app.name}
    >
      <IconComponent size={20} className="text-shell-text" />
      {isRunning && (
        <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-accent" />
      )}
    </button>
  );
}
