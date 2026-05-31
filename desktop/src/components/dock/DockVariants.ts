import { MacosDock } from "./MacosDock";
import { WindowsTaskbar } from "./WindowsTaskbar";

export const DOCK_VARIANTS = {
  "macos-dock": MacosDock,
  "windows-taskbar": WindowsTaskbar,
} as const;

export type DockVariantId = keyof typeof DOCK_VARIANTS;
