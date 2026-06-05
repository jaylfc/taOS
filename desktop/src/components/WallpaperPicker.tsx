import { useThemeStore } from "@/stores/theme-store";
import { Check } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function WallpaperPicker({ open, onClose }: Props) {
  const { wallpaperId, setWallpaper, getWallpapers } = useThemeStore();
  const wallpapers = getWallpapers();

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[10002] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onClick={onClose}
      style={{
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 16px)",
        paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35 + 16px)",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Change Wallpaper"
        className="w-full max-w-[500px] max-h-full flex flex-col rounded-xl border border-shell-border-strong overflow-hidden"
        style={{ backgroundColor: "rgba(26, 27, 46, 0.98)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-shell-border shrink-0">
          <h3 className="text-sm font-medium text-shell-text">Change Wallpaper</h3>
          <button
            onClick={onClose}
            className="text-shell-text-tertiary hover:text-shell-text text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="p-4 grid grid-cols-2 gap-3 overflow-y-auto flex-1">
          {wallpapers.map((wp) => (
            <button
              key={wp.id}
              onClick={() => {
                setWallpaper(wp.id);
              }}
              aria-label={wp.label}
              aria-pressed={wallpaperId === wp.id}
              className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                wallpaperId === wp.id
                  ? "border-accent ring-1 ring-accent/30"
                  : "border-shell-border hover:border-shell-border-strong"
              }`}
            >
              <div
                className="h-24 w-full"
                style={{
                  backgroundImage: wp.image,
                  backgroundColor: wp.fallback,
                  backgroundSize: "cover",
                  backgroundPosition: "center",
                  backgroundRepeat: "no-repeat",
                }}
              />
              <div className="px-2 py-1.5 text-xs text-shell-text-secondary text-left">
                {wp.label}
              </div>
              {wallpaperId === wp.id && (
                <div className="absolute top-2 right-2 w-5 h-5 rounded-full bg-accent flex items-center justify-center">
                  <Check size={12} className="text-white" />
                </div>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
