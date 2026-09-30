import { useState, useEffect, useRef, type FormEvent } from "react";
import { AlertCircle, AlertTriangle, Loader2, Lock } from "lucide-react";
import { Button, Card, Input, Label } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

type Method = "swipe" | "pin" | "password";

interface LockState {
  unlock_method: Method;
  has_pin: boolean;
  swipe_available: boolean;
}

const PIN_MIN = 4;
const PIN_MAX = 12;

const OPTIONS: { id: Method | "pattern"; label: string; desc: string }[] = [
  { id: "swipe", label: "Swipe", desc: "Swipe up on the lock screen to open taOS. No code." },
  { id: "pin", label: "PIN", desc: `${PIN_MIN}-${PIN_MAX} digits on the lock screen keypad.` },
  { id: "password", label: "Password", desc: "Your account password." },
  { id: "pattern", label: "Pattern", desc: "Coming soon" },
];

export const SWIPE_WARNING = "Anyone holding this device can open taOS.";

/** Settings -> Lock screen: how this device's own screen unlocks.
 *
 *  Changing the method always costs the CURRENT account password: the server
 *  refuses without it, and Swipe in particular turns a moment at an unlocked
 *  screen into a permanent way in. Choosing PIN with no PIN set collects one
 *  first (POST /auth/pin, which needs the same password). */
export function LockScreenPanel() {
  const [state, setState] = useState<LockState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [choice, setChoice] = useState<Method | null>(null);
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/settings/lock", { credentials: "include" });
        const body = await res.json().catch(() => null);
        if (!mounted.current) return;
        if (!res.ok || !body?.unlock_method) {
          setLoadError("Could not load the lock screen setting.");
          return;
        }
        setState(body);
        setChoice(body.unlock_method);
      } catch {
        if (mounted.current) setLoadError("Could not reach taOS.");
      }
    })();
  }, []);

  const changing = state !== null && choice !== null && choice !== state.unlock_method;
  const needsPin = changing && choice === "pin" && !state?.has_pin;

  const reset = () => { setPassword(""); setPin(""); setConfirm(""); setError(null); };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || !state || !choice) return;
    if (needsPin) {
      if (!/^\d+$/.test(pin) || pin.length < PIN_MIN || pin.length > PIN_MAX) {
        setError(`A PIN must be ${PIN_MIN}-${PIN_MAX} digits.`);
        return;
      }
      if (pin !== confirm) { setError("The two PINs do not match."); return; }
    }
    if (!password) { setError("Enter your current password to confirm."); return; }
    setBusy(true);
    setError(null);
    try {
      let hasPin = state.has_pin;
      if (needsPin) {
        const r = await fetch("/auth/pin", withCsrf({
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pin, password }),
        }));
        const b = await r.json().catch(() => null);
        if (!mounted.current) return;
        if (!r.ok || !b?.ok) { setError(b?.error || "Could not save the PIN."); return; }
        hasPin = true;
      }
      const res = await fetch("/api/settings/lock", withCsrf({
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unlock_method: choice, current_password: password }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || !body?.ok) {
        // Keep the PIN we may have just set reflected, even if the switch failed.
        if (hasPin !== state.has_pin) setState({ ...state, has_pin: hasPin });
        setError(body?.error || "Could not change the lock screen.");
        return;
      }
      setState({
        unlock_method: body.unlock_method,
        has_pin: body.has_pin,
        swipe_available: body.swipe_available,
      });
      setChoice(body.unlock_method);
      reset();
      setStatus("Lock screen updated.");
    } catch {
      if (mounted.current) setError("Could not reach taOS. Check the connection and try again.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <section aria-label="Lock screen settings">
      <h2 className="text-lg font-semibold mb-2">Lock screen</h2>
      <p className="text-sm text-shell-text-tertiary mb-5">
        How this device's own screen unlocks. Over the network your password is
        always required.
      </p>

      {loadError && (
        <p role="alert" className="text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {loadError}
        </p>
      )}

      {state && (
        <form onSubmit={save} className="space-y-3">
          <fieldset className="space-y-2">
            <legend className="sr-only">Unlock method</legend>
            {OPTIONS.map((opt) => {
              const planned = opt.id === "pattern";
              const noSwipe = opt.id === "swipe" && !state.swipe_available;
              const disabled = planned || noSwipe || busy;
              const id = `unlock-${opt.id}`;
              return (
                <Card key={opt.id} className={`p-4 flex items-start gap-3 ${disabled && !busy ? "opacity-60" : ""}`}>
                  <input
                    type="radio"
                    id={id}
                    name="unlock-method"
                    value={opt.id}
                    className="mt-1"
                    checked={choice === opt.id}
                    disabled={disabled}
                    onChange={() => {
                      if (planned) return;
                      setChoice(opt.id as Method);
                      setStatus("");
                      reset();
                    }}
                  />
                  <div className="min-w-0">
                    <Label htmlFor={id} className="text-sm font-medium text-shell-text">
                      {opt.label}
                    </Label>
                    <p className="text-xs text-shell-text-tertiary mt-0.5">
                      {noSwipe ? "Only available when this device has a single account." : opt.desc}
                    </p>
                  </div>
                </Card>
              );
            })}
          </fieldset>

          {changing && choice === "swipe" && (
            <p role="note" className="text-xs text-amber-400 flex items-center gap-1.5">
              <AlertTriangle size={12} /> {SWIPE_WARNING}
            </p>
          )}

          {changing && (
            <Card className="p-4 space-y-3">
              {needsPin && (
                <>
                  <div>
                    <Label htmlFor="lock-pin-new">New PIN</Label>
                    <Input id="lock-pin-new" type="password" inputMode="numeric" autoComplete="off"
                           maxLength={PIN_MAX} value={pin}
                           onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
                  </div>
                  <div>
                    <Label htmlFor="lock-pin-confirm">Confirm PIN</Label>
                    <Input id="lock-pin-confirm" type="password" inputMode="numeric" autoComplete="off"
                           maxLength={PIN_MAX} value={confirm}
                           onChange={(e) => setConfirm(e.target.value.replace(/\D/g, ""))} />
                  </div>
                </>
              )}
              <div>
                <Label htmlFor="lock-current-password">Current password</Label>
                <Input id="lock-current-password" type="password" autoComplete="current-password"
                       value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              {error && (
                <p className="text-xs text-amber-400 flex items-center gap-1.5" role="alert">
                  <AlertCircle size={12} /> {error}
                </p>
              )}
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={busy}>
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <><Lock size={14} /> Save</>}
                </Button>
                <Button type="button" size="sm" variant="outline" disabled={busy}
                        onClick={() => { setChoice(state.unlock_method); reset(); }}>
                  Cancel
                </Button>
              </div>
            </Card>
          )}

          <p aria-live="polite" className="text-xs text-emerald-400">{status}</p>
        </form>
      )}
    </section>
  );
}
