import { useEffect, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { isAuthError, login, register } from "@/lib/account-client";
import { OobePage } from "../OobeShell";

export type AccountMode = "signin" | "register";

/**
 * Optional taOS account (taos.my): use an existing one, create one, or stay
 * offline. Offline keeps everything on this device, and Settings > Account
 * offers the same sign-in and sign-up later. This step is identity only: what
 * syncs after sign-in is the device step's job
 * (docs/proposals/account-device-sync.md).
 */
export function TaosAccountChoice({
  onChoose,
  onOffline,
}: {
  onChoose: (mode: AccountMode) => void;
  onOffline: () => void;
}) {
  const options: { id: string; title: string; detail: string; pick: () => void }[] = [
    {
      id: "signin",
      title: "Sign in with your taOS account",
      detail: "Link this device to the account you already have.",
      pick: () => onChoose("signin"),
    },
    {
      id: "register",
      title: "Create a taOS account",
      detail: "Free. Reach this device away from home.",
      pick: () => onChoose("register"),
    },
    {
      id: "offline",
      title: "Use offline only",
      detail: "Everything stays on this device. Sign in later in Settings.",
      pick: onOffline,
    },
  ];
  return (
    <OobePage
      title="Your taOS account"
      subtitle="A taOS account lets you reach this device from anywhere and link your other taOS devices."
      onSubmit={() => {}}
      footer={null}
    >
      <div className="oobe-group">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            className="oobe-choice"
            onClick={o.pick}
          >
            <span className="oobe-choice-text">
              <span className="oobe-choice-title">{o.title}</span>
              <span className="oobe-choice-detail">{o.detail}</span>
            </span>
            <ChevronRight size={20} aria-hidden="true" className="oobe-choice-chevron" />
          </button>
        ))}
      </div>
    </OobePage>
  );
}

export function TaosAccountForm({
  mode,
  onDone,
  onBack,
}: {
  mode: AccountMode;
  onDone: () => void;
  onBack: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [signupsClosed, setSignupsClosed] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const valid = email.trim().length > 0 && password.length >= 8;

  async function submit() {
    if (!valid || busy) return;
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

  const signin = mode === "signin";
  return (
    <OobePage
      title={signin ? "Sign in to taOS" : "Create your taOS account"}
      subtitle={
        signin
          ? "Use the email and password of your taOS account."
          : "You'll use this email and password on every taOS device you link."
      }
      onBack={busy ? undefined : onBack}
      onSubmit={submit}
      footer={
        !signupsClosed && (
          <button type="submit" className="oobe-primary" disabled={!valid || busy}>
            {busy ? "Please wait..." : signin ? "Sign in" : "Create account"}
          </button>
        )
      }
    >
      {signupsClosed ? (
        <p className="oobe-status" role="status">
          New taOS accounts aren't open yet. Go back and choose offline for now: you can
          create one later in Settings, Account.
        </p>
      ) : (
        <>
          <div className="oobe-group">
            <div className="oobe-row">
              <label htmlFor="oobe-taos-email">Email</label>
              <input
                id="oobe-taos-email"
                className="oobe-input"
                type="email"
                inputMode="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="name@example.com"
                autoFocus
              />
            </div>
            <div className="oobe-row">
              <label htmlFor="oobe-taos-password">Password</label>
              <input
                id="oobe-taos-password"
                className="oobe-input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={signin ? "current-password" : "new-password"}
                placeholder="Required"
                aria-label="taOS account password"
              />
            </div>
          </div>
          {error ? (
            <p className="oobe-error" role="alert">{error}</p>
          ) : (
            !signin && <p className="oobe-note">At least 8 characters.</p>
          )}
        </>
      )}
    </OobePage>
  );
}
