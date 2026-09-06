import { useState } from "react";
import { LayoutGrid } from "lucide-react";
import type { InstalledService } from "@/hooks/use-installed-services";

interface Props {
  service: InstalledService;
  onClick: () => void;
}

/**
 * Launchpad-style icon tile for an installed service.
 * Matches the LaunchpadIcon sizing and shape (w-14 h-14 rounded-2xl).
 * Falls back to a generic grid icon if the image URL 404s.
 */
export function ServiceIcon({ service, onClick }: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const showImg = service.icon && !imgFailed;

  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center gap-2 p-3 rounded-xl hover:bg-white/5 transition-colors"
      aria-label={`Open ${service.display_name}`}
    >
      <div className="w-14 h-14 rounded-2xl bg-shell-surface-hover flex items-center justify-center overflow-hidden">
        {showImg ? (
          <img
            src={service.icon!}
            alt={service.display_name}
            className="w-8 h-8 object-contain text-shell-text"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <LayoutGrid size={28} className="text-shell-text" />
        )}
      </div>
      <span className="text-xs text-shell-text-secondary text-center max-w-[72px] truncate">
        {service.display_name}
      </span>
    </button>
  );
}
