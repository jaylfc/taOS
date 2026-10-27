import { useState, useEffect } from "react";
import { Card } from "@/components/ui";

export function BluetoothPanel() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [adapters, setAdapters] = useState<{ path: string; address: string; powered: boolean }[]>([]);
  const [devices, setDevices] = useState<{ path: string; address: string; name: string | null; paired: boolean; connected: boolean; trusted: boolean }[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await fetch("/api/hardware/bluetooth", { credentials: "include" });
        const body = await res.json().catch(() => null);
        if (!mounted) return;
        if (!res.ok || body === null || typeof body.available !== "boolean") {
          setError("Could not load Bluetooth state.");
          return;
        }
        setAvailable(body.available);
        if (Array.isArray(body.adapters)) {
          setAdapters(body.adapters);
        }
        if (Array.isArray(body.devices)) {
          setDevices(body.devices);
        }
        setError(null);
      } catch {
        if (mounted) setError("Could not reach taOS.");
      }
    };
    load();
    const id = setInterval(load, 10_000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  return (
    <section aria-label="Bluetooth settings">
      <h2 className="text-lg font-semibold mb-2">Bluetooth</h2>
      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400">
          {error}
        </p>
      )}
      {available === null && !error && null}
      {available === false && (
        <Card className="p-4">
          Bluetooth controls need BlueZ, which is not running on this device.
        </Card>
      )}
      {available === true && adapters.length === 0 && (
        <Card className="p-4">No Bluetooth adapter found.</Card>
      )}
      {available === true && adapters.length > 0 && adapters.every((a) => a.powered === false) && (
        <Card className="p-4">Bluetooth is turned off.</Card>
      )}
      {available === true && adapters.length > 0 && adapters.some((a) => a.powered === true) && (
        <ul aria-label="Bluetooth devices">
          {devices.length === 0 ? (
            <li className="text-sm text-shell-text-tertiary">No devices yet.</li>
          ) : (
            devices.map((device) => (
              <li key={device.path || device.address} className="flex items-center gap-2 text-sm">
                <span className="flex-1 min-w-0 truncate">
                  {device.name || device.address}
                </span>
                {device.connected && (
                  <span className="text-xs text-emerald-400">Connected</span>
                )}
                {!device.connected && device.paired && (
                  <span className="text-xs text-shell-text-tertiary">Paired</span>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </section>
  );
}
