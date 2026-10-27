import { useState, useEffect, useRef } from "react";
import { AlertCircle, Wifi as WifiIcon } from "lucide-react";
import { Card } from "@/components/ui";

export function WifiPanel() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [wifiEnabled, setWifiEnabled] = useState<boolean | null>(null);
  const [accessPoints, setAccessPoints] = useState<{ ssid: string; strength: number; frequency: number; secured: boolean; active: boolean }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const load = async () => {
      try {
        const res = await fetch("/api/hardware/wifi", { credentials: "include" });
        const body = await res.json().catch(() => null);
        if (!mounted.current) return;
        if (!res.ok || body === null || typeof body.available !== "boolean") {
          setError("Could not load Wi-Fi state.");
          return;
        }
        setAvailable(body.available);
        setWifiEnabled(body.wifi_enabled ?? null);
        if (body.available && body.wifi_enabled && Array.isArray(body.access_points)) {
          setAccessPoints(body.access_points);
        }
        setError(null);
      } catch {
        if (mounted.current) setError("Could not reach taOS.");
      }
    };
    load();
    const id = setInterval(load, 10_000);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, []);

  return (
    <section aria-label="Wi-Fi settings">
      <h2 className="text-lg font-semibold mb-2">Wi-Fi</h2>
      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      {available === false && (
        <Card className="p-4">
          Wi-Fi controls need NetworkManager, which is not running on this device.
        </Card>
      )}
      {available === true && wifiEnabled === false && (
        <Card className="p-4">Wi-Fi is turned off.</Card>
      )}
      {available === true && wifiEnabled === true && (
        <ul aria-label="Wi-Fi networks" className="space-y-2">
          {accessPoints.length === 0 ? (
            <li className="text-sm text-shell-text-tertiary">No networks found.</li>
          ) : (
            accessPoints.map((ap) => (
              <li key={ap.ssid || `hidden-${ap.frequency}`} className="flex items-center gap-2 text-sm">
                <WifiIcon size={14} className="text-shell-text-secondary" />
                <span className="flex-1 min-w-0 truncate">
                  {ap.ssid || "Hidden network"}
                </span>
                {ap.active && (
                  <span className="text-xs text-emerald-400">Connected</span>
                )}
                {ap.secured && !ap.active && (
                  <span className="text-xs text-shell-text-tertiary">Secured</span>
                )}
                <span className="text-xs text-shell-text-tertiary tabular-nums" aria-label={`Signal ${ap.strength} percent`}>
                  {ap.strength}%
                </span>
              </li>
            ))
          )}
        </ul>
      )}
    </section>
  );
}