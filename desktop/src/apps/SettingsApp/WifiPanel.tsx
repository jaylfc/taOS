import { useState, useEffect, useRef, useCallback } from "react";
import { AlertCircle, Wifi as WifiIcon, Eye, EyeOff, Loader2, X } from "lucide-react";
import { Card, Switch, Button, Input, Label } from "@/components/ui";

export function WifiPanel() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [wifiEnabled, setWifiEnabled] = useState<boolean | null>(null);
  const [accessPoints, setAccessPoints] = useState<{ ssid: string; strength: number; frequency: number; secured: boolean; active: boolean }[]>([]);
  const [devices, setDevices] = useState<{ path: string; iface: string; state: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [connectingSsid, setConnectingSsid] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const passwordInputRef = useRef<HTMLInputElement>(null);
  const joinButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const seq = useRef(0);
  const active = useRef(true);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const res = await fetch("/api/hardware/wifi", { credentials: "include" });
      if (!active.current || mine !== seq.current) return;
      const body = await res.json().catch(() => null);
      if (!active.current || mine !== seq.current) return;
      if (!res.ok || body === null || typeof body.available !== "boolean") {
        if (!active.current || mine !== seq.current) return;
        setError("Could not load Wi-Fi state.");
        return;
      }
      if (!active.current || mine !== seq.current) return;
      setAvailable(body.available);
      if (!active.current || mine !== seq.current) return;
      setWifiEnabled(body.wifi_enabled ?? null);
      if (body.available && body.wifi_enabled && Array.isArray(body.access_points)) {
        if (!active.current || mine !== seq.current) return;
        setAccessPoints(body.access_points);
      }
      if (!active.current || mine !== seq.current) return;
      if (Array.isArray(body.devices)) {
        setDevices(body.devices);
      }
      if (!active.current || mine !== seq.current) return;
      setError(null);
    } catch {
      if (!active.current || mine !== seq.current) return;
      setError("Could not reach taOS.");
    }
  }, []);

  useEffect(() => {
    active.current = true;
    seq.current = 0;
    load();
    const id = setInterval(load, 10_000);
    return () => {
      active.current = false;
      clearInterval(id);
    };
  }, [load]);

  const handleToggleWifi = async (enabled: boolean) => {
    setStatus(enabled ? "Turning Wi-Fi on..." : "Turning Wi-Fi off...");
    setError(null);
    try {
      const res = await fetch("/api/hardware/wifi/enabled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ enabled }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        const msg = body?.error ? `Wi-Fi toggle failed: ${body.error}` : `Wi-Fi toggle failed (${res.status})`;
        setError(msg);
        return;
      }
      setStatus(null);
      load();
    } catch {
      setError("Could not reach taOS.");
      setStatus(null);
    }
  };

  const handleDisconnect = async (iface: string) => {
    setStatus("Disconnecting...");
    setError(null);
    try {
      const res = await fetch("/api/hardware/wifi/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ iface }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        const msg = body?.error ? `Disconnect failed: ${body.error}` : `Disconnect failed (${res.status})`;
        setError(msg);
        return;
      }
      setStatus(null);
      load();
    } catch {
      setError("Could not reach taOS.");
      setStatus(null);
    }
  };

  const handleJoinClick = (ssid: string, secured: boolean) => {
    if (secured) {
      setConnectingSsid(ssid);
      setShowPassword(null);
      setPassword("");
      setPasswordError(null);
      setTimeout(() => passwordInputRef.current?.focus(), 0);
    } else {
      setStatus(`Joining ${ssid}...`);
      handleConnect(ssid, "", false);
    }
  };

  const handleConnect = async (ssid: string, psk: string, secured: boolean) => {
    setConnectingSsid(ssid);
    if (psk) {
      setStatus(`Connecting to ${ssid}...`);
    }
    setSubmitting(true);
    setError(null);
    setPasswordError(null);
    try {
      const res = await fetch("/api/hardware/wifi/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ ssid, psk: psk || undefined }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        const msg = body?.error ? `Connection failed: ${body.error}` : `Connection failed (${res.status})`;
        if (body?.error === "not_permitted") {
          setError(msg);
          setConnectingSsid(null);
          setShowPassword(null);
          setPassword("");
          setStatus(null);
          setSubmitting(false);
        } else if (!secured) {
          setError(msg);
          setConnectingSsid(null);
          setStatus(null);
          setSubmitting(false);
        } else {
          setPasswordError(msg);
          setStatus(null);
          setSubmitting(false);
        }
        return;
      }
      setConnectingSsid(null);
      setShowPassword(null);
      setPassword("");
      setStatus(null);
      setSubmitting(false);
      load();
    } catch {
      setError("Could not reach taOS.");
      setConnectingSsid(null);
      setShowPassword(null);
      setPassword("");
      setStatus(null);
      setSubmitting(false);
    }
  };

  const handlePasswordKeyDown = (e: React.KeyboardEvent, ssid: string) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleConnect(ssid, password, true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setConnectingSsid(null);
      setShowPassword(null);
      setPassword("");
      setPasswordError(null);
      const btn = joinButtonRefs.current.get(ssid);
      btn?.focus();
    }
  };

  const handlePasswordChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setPassword(e.target.value);
    setPasswordError(null);
  };

  const getIface = () => {
    return devices[0]?.iface ?? "wlan0";
  };

  return (
    <section aria-label="Wi-Fi settings">
      <h2 className="text-lg font-semibold mb-2">Wi-Fi</h2>
      <div className="flex items-center gap-3 mb-4">
        <Switch
          id="wifi-toggle"
          checked={wifiEnabled === true}
          onCheckedChange={handleToggleWifi}
          disabled={available !== true || wifiEnabled === null}
          aria-label="Wi-Fi"
        />
        <Label htmlFor="wifi-toggle" className="text-sm font-medium text-shell-text">
          Wi-Fi
        </Label>
      </div>
      {status && <p aria-live="polite" className="mb-3 text-xs text-sky-400">{status}</p>}
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
            accessPoints.map((ap) => {
              const isConnecting = connectingSsid === ap.ssid;
              const isPasswordVisible = showPassword === ap.ssid;
              const key = ap.ssid || `hidden-${ap.frequency}`;
              return (
                <li key={key} className="flex items-center gap-2 text-sm">
                  <WifiIcon size={14} className="text-shell-text-secondary" />
                  <span className="flex-1 min-w-0 truncate">
                    {ap.ssid || "Hidden network"}
                  </span>
                  {ap.active && (
                    <>
                      <span className="text-xs text-emerald-400">Connected</span>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => handleDisconnect(getIface())}
                        disabled={isConnecting}
                        aria-label={`Disconnect from ${ap.ssid || "this network"}`}
                      >
                        Disconnect
                      </Button>
                    </>
                  )}
                  {!ap.active && (
                    <Button
                      ref={(el) => {
                        if (el) joinButtonRefs.current.set(ap.ssid, el);
                        else joinButtonRefs.current.delete(ap.ssid);
                      }}
                      variant="outline"
                      size="sm"
                      onClick={() => handleJoinClick(ap.ssid, ap.secured)}
                      disabled={isConnecting}
                      aria-label={ap.secured ? `Join ${ap.ssid} (secured)` : `Join ${ap.ssid}`}
                    >
                      {isConnecting && ap.secured ? (
                        <>
                          <Loader2 size={12} className="animate-spin" />
                          Connecting
                        </>
                      ) : isConnecting ? (
                        <>
                          <Loader2 size={12} className="animate-spin" />
                          Joining
                        </>
                      ) : (
                        "Join"
                      )}
                    </Button>
                  )}
                  {ap.secured && !ap.active && (
                    <span className="text-xs text-shell-text-tertiary">Secured</span>
                  )}
                  <span className="text-xs text-shell-text-tertiary tabular-nums" aria-label={`Signal ${ap.strength} percent`}>
{ap.strength}%
                  </span>
                  {isConnecting && ap.secured && !ap.active && (
                    <div className="flex items-center gap-2 ml-2">
                      <Label htmlFor={`password-${ap.ssid}`} className="sr-only">
                        Password for {ap.ssid}
                      </Label>
                      <div className="relative">
                        <Input
                          ref={passwordInputRef}
                          id={`password-${ap.ssid}`}
                          type={isPasswordVisible ? "text" : "password"}
                          value={password}
                          onChange={handlePasswordChange}
                          onKeyDown={(e) => handlePasswordKeyDown(e, ap.ssid)}
                          placeholder="Password"
                          className="w-48"
                          aria-label={`Password for ${ap.ssid}`}
                          disabled={submitting}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 p-0"
                          onClick={() => setShowPassword(isPasswordVisible ? null : ap.ssid)}
                          aria-label={isPasswordVisible ? "Hide password" : "Show password"}
                          tabIndex={-1}
                        >
                          {isPasswordVisible ? <EyeOff size={14} /> : <Eye size={14} />}
                        </Button>
                      </div>
                      {passwordError && (
                        <p className="text-xs text-amber-400">{passwordError}</p>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 p-0"
                        onClick={() => {
                          setConnectingSsid(null);
                          setShowPassword(null);
                          setPassword("");
                          setPasswordError(null);
                          const btn = joinButtonRefs.current.get(ap.ssid);
                          btn?.focus();
                        }}
                        aria-label="Cancel"
                        tabIndex={-1}
                      >
                        <X size={14} />
                      </Button>
                    </div>
                  )}
                </li>
              );
            })
          )}
        </ul>
      )}
    </section>
  );
}