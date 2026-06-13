import { Suspense, lazy, useMemo } from "react";
import { X, Minus } from "lucide-react";
import { getApp } from "@/registry/app-registry";

interface Props {
  appId: string;
  windowId: string;
  onClose: () => void;
  onMinimise: () => void;
}

export function MobileAppWindow({ appId, windowId, onClose, onMinimise }: Props) {
  const app = getApp(appId);
  const LazyComponent = useMemo(() => {
    if (!app) return null;
    return lazy(app.component);
  }, [app]);

  if (!app || !LazyComponent) return null;

  return (
    <div className="flex flex-col h-full w-full">
      {/* Title bar */}
      <div
        className="flex items-center px-3 gap-2 shrink-0"
        style={{
          height: "32px",
          background: "rgba(28, 28, 31, 0.9)",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {/* Window controls */}
        <button
          onClick={onClose}
          aria-label="Close"
          className="flex items-center justify-center rounded-full shrink-0"
          style={{
            width: "18px",
            height: "18px",
            background: "rgba(255,80,80,0.3)",
            border: "1px solid rgba(255,80,80,0.3)",
          }}
        >
          <X size={10} style={{ color: "rgba(255,120,120,0.8)" }} />
        </button>

        <button
          onClick={onMinimise}
          aria-label="Minimise"
          className="flex items-center justify-center rounded-full shrink-0"
          style={{
            width: "18px",
            height: "18px",
            background: "rgba(255,200,50,0.2)",
            border: "1px solid rgba(255,200,50,0.3)",
          }}
        >
          <Minus size={10} style={{ color: "rgba(255,200,80,0.8)" }} />
        </button>

        {/* App name — centred */}
        <div
          className="flex-1 text-center text-xs font-medium"
          style={{ color: "rgba(255,255,255,0.6)" }}
        >
          {app.name}
        </div>

        {/* Right spacer for visual balance */}
        <div style={{ width: "44px" }} />
      </div>

      {/* App content */}
      <div
        className="flex-1 overflow-hidden"
        style={{ background: "rgba(15,15,35,0.95)" }}
      >
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-full text-white/30 text-sm">
              Loading...
            </div>
          }
        >
          <LazyComponent windowId={windowId} />
        </Suspense>
      </div>
    </div>
  );
}
