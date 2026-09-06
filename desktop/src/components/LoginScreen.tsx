import { useState } from "react";
import { Button } from "@/components/ui";

function getBrowserTier(): "full" | "safari" | "limited" {
  const ua = navigator.userAgent;
  if (/Chrome|Chromium|Edg|OPR/i.test(ua) && !/Safari/i.test(ua)) return "full";
  if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) return "safari";
  return "limited";
}

export function LoginScreen({ onLaunch }: { onLaunch: () => void }) {
  const [launching, setLaunching] = useState(false);
  const tier = getBrowserTier();

  const handleLaunch = async () => {
    setLaunching(true);
    try {
      await document.documentElement.requestFullscreen();
    } catch {
      // Fullscreen may not be available in some contexts
    }
    setTimeout(onLaunch, 600);
  };

  return (
    <div
      className={`fixed inset-0 z-[9999] bg-black flex items-center justify-center transition-all duration-[600ms] ${
        launching ? "scale-[1.15] opacity-0" : "scale-100 opacity-100"
      }`}
    >
      {/* Radial tunnel overlay for warp effect */}
      <div
        className={`absolute inset-0 transition-opacity duration-[600ms] ${
          launching ? "opacity-60" : "opacity-0"
        }`}
        style={{
          background: "radial-gradient(circle at center, transparent 0%, transparent 30%, rgba(100,100,255,0.15) 60%, rgba(50,50,200,0.3) 100%)",
        }}
      />

      <div className="text-center space-y-8 relative z-10">
        <img
          src="/static/taos-logo.png"
          alt="taOS"
          className="w-64 mx-auto"
        />

        <div className="space-y-4">
          <Button
            size="lg"
            onClick={handleLaunch}
            disabled={launching}
            className="text-lg px-10 py-5 rounded-xl bg-white/10 hover:bg-white/20 border border-white/20 text-white font-medium transition-all"
            aria-label="Launch taOS"
          >
            {launching ? "Launching..." : "Launch taOS"}
          </Button>
        </div>

        {tier === "full" && (
          <p className="text-xs text-green-400/70">Full experience available</p>
        )}
        {tier === "safari" && (
          <p className="text-xs text-yellow-400/70">Install taOS as an app for the best experience</p>
        )}
        {tier === "limited" && (
          <p className="text-xs text-white/40">For full keyboard support, use Chrome or Edge</p>
        )}
      </div>
    </div>
  );
}
