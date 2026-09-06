import { useState, useEffect } from "react";

export type DeviceMode = "desktop" | "tablet" | "mobile";

function detectMode(): DeviceMode {
  const width = window.innerWidth;
  const isTouch = matchMedia("(pointer: coarse)").matches || navigator.maxTouchPoints > 0;
  if (width < 768) return "mobile";
  if (width < 1024 && isTouch) return "tablet";
  return "desktop";
}

export function useDeviceMode(): DeviceMode {
  const [mode, setMode] = useState<DeviceMode>(detectMode);

  useEffect(() => {
    const handler = () => setMode(detectMode());
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  return mode;
}
