import { useEffect, useRef, useState, type FormEvent } from "react";
import { Cloud } from "lucide-react";
import { isAuthError, login, register } from "@/lib/account-client";

interface Props {
  onDone: () => void;
  /** Prefills the email from the local account step. */
  defaultEmail?: string;
}

/**
 * First-run step 3: optional taOS account (taos.my). Signing in links this
 * device to the account; skipping keeps everything local, and Settings >
 * Account offers the same sign-in later. This step is identity only: what
 * syncs after sign-in is the device-approval step's job
 * (docs/proposals/account-device-sync.md).
 */
export function TaosAccountStep({ onDone, defaultEmail }: Props) {
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState(defaultEmail ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [signupsClosed, setSignupsClosed] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const valid = email.trim().length > 0 && password.length >= 8;

  function switchMode(next: "signin" | "register") {
    setMode(next);
    setError("");
    setSignupsClosed(false);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError("");
    const result = await (mode === "signin" ? login : register)(email.trim(), password);
    if (!mounted.current) return;
    setBusy(false);
    if (!isAuthError(result)) {
      onDone();
      return;
    }
    if (result.code === "signups_closed") setSignupsClosed(true);
    else setError(result.message);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-md p-6 rounded-2xl border border-white/10"
      style={{
        backgroundColor: "rgba(255,255,255,0.04)",
        backdropFilter: "blur(20px)",
      }}
      aria-label="Your taOS account"
    >
      <div className="flex flex-col items-center gap-3 mb-6">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
        >
          <Cloud size={24} className="text-white" />
        </div>
        <h1 className="text-lg font-semibold text-shell-text">Your taOS account</h1>
        <p className="text-xs text-shell-text-secondary text-center">
          Optional. A taOS account lets you reach this device away from home and
          link your other taOS devices. Skip it and everything stays on this device.
        </p>
      </div>

      <div className="flex gap-2 mb-4" role="group" aria-label="Account action">
        {(["signin", "register"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => switchMode(m)}
            aria-pressed={mode === m}
            className={`flex-1 px-3 py-1.5 rounded-lg text-xs border transition-colors ${
              mode === m
                ? "border-white/20 bg-white/10 text-shell-text"
                : "border-white/10 text-shell-text-secondary hover:bg-white/5"
            }`}
          >
            {m === "signin" ? "Sign in" : "Create account"}
          </button>
        ))}
      </div>

      {signupsClosed ? (
        <p className="text-xs text-shell-text-secondary text-center py-4" role="status">
          New taOS accounts aren't open yet. Skip for now: you can sign in later
          from Settings, Account.
        </p>
      ) : (
        <div className="space-y-3">
          <div>
            <label
              htmlFor="onb-taos-email"
              className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1"
            >
              Email
            </label>
            <input
              id="onb-taos-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="you@example.com"
              className="onb-input"
            />
          </div>
          <div>
            <label
              htmlFor="onb-taos-password"
              className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1"
            >
              taOS account password
            </label>
            <input
              id="onb-taos-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              className="onb-input"
            />
            <p className="text-[10px] text-shell-text-tertiary mt-1">At least 8 characters.</p>
          </div>
        </div>
      )}

      {error && (
        <p className="text-xs text-red-400 mt-3 text-center" role="alert">
          {error}
        </p>
      )}

      {!signupsClosed && (
        <button
          type="submit"
          disabled={!valid || busy}
          className="w-full mt-5 px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {busy ? "Please wait..." : mode === "signin" ? "Sign in to taOS" : "Create taOS account"}
        </button>
      )}

      <button
        type="button"
        onClick={onDone}
        disabled={busy}
        className="w-full mt-2 px-4 py-2.5 rounded-lg text-sm text-shell-text-secondary hover:bg-white/5 disabled:opacity-40 transition-colors"
      >
        Skip for now
      </button>
    </form>
  );
}
