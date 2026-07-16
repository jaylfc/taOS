import { useState, type FormEvent } from "react";
import { Sparkles, AtSign, Check } from "lucide-react";

interface Props {
  onDone: () => void;
  /** When set, this is an invited user completing their account (not first-run). */
  invitedUsername?: string;
  inviteCode?: string;
  /** Default for the auto-login checkbox. False in multi-user mode. */
  defaultAutoLogin?: boolean;
}

type Step = "account" | "username";

/**
 * First-run onboarding (no props) or invite completion (invitedUsername + inviteCode).
 *
 * In invite mode:
 * - Title becomes "Complete your account"
 * - Username field is read-only
 * - Submit POSTs to /auth/complete instead of /auth/setup
 * - auto-login defaults to false
 *
 * After the local account is created the flow advances to the free taOS username
 * step (slice 5 of the account model). The username is free and optional here, so
 * the user can always finish without touching the paid taOSgo path (subdomain
 * publishing is deferred to Settings and never blocks onboarding).
 */
export function OnboardingScreen({
  onDone,
  invitedUsername,
  inviteCode,
  defaultAutoLogin,
}: Props) {
  const isInvite = Boolean(invitedUsername && inviteCode);
  // The username-claim step is gated off (see handleSubmit); step stays "account".
  const [step] = useState<Step>("account");

  const [username, setUsername] = useState(invitedUsername ?? "");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [autoLogin, setAutoLogin] = useState(defaultAutoLogin ?? !isInvite);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const passwordOk = password.length >= 4;
  const matches = password.length > 0 && password === confirm;
  const valid =
    username.trim().length > 0 &&
    fullName.trim().length > 0 &&
    passwordOk &&
    matches;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setLoading(true);
    setError("");
    try {
      const endpoint = isInvite ? "/auth/complete" : "/auth/setup";
      const body: Record<string, unknown> = {
        username: username.trim(),
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        auto_login: autoLogin,
      };
      if (isInvite) {
        body.invite_code = inviteCode;
      }
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? `Setup failed (${res.status})`);
        setLoading(false);
        return;
      }
      // Account created: finish onboarding. The taos.my username-claim step is
      // gated off until its backend route exists (tracked in #141): advancing to
      // it would POST to /api/account/username, which is not implemented yet, so
      // every user would hit an error. Re-enable by restoring setStep("username").
      onDone();
    } catch {
      setError("Network error — please try again");
      setLoading(false);
    }
  }

  if (step === "username") {
    return <UsernameStep defaultName={username} onDone={onDone} />;
  }

  return (
    <div
      className="h-screen w-screen flex items-center justify-center overflow-y-auto"
      style={{
        background: "var(--color-shell-bg)",
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 16px)",
        paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 16px)",
        paddingLeft: "calc(env(safe-area-inset-left, 0px) + 16px)",
        paddingRight: "calc(env(safe-area-inset-right, 0px) + 16px)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md p-6 rounded-2xl border border-white/10"
        style={{
          backgroundColor: "rgba(255,255,255,0.04)",
          backdropFilter: "blur(20px)",
        }}
        aria-label={isInvite ? "Complete your account" : "Welcome to taOS"}
      >
        <div className="flex flex-col items-center gap-3 mb-6">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
          >
            <Sparkles size={24} className="text-white" />
          </div>
          <h1 className="text-lg font-semibold text-shell-text">
            {isInvite ? "Complete your account" : "Welcome to taOS"}
          </h1>
          <p className="text-xs text-shell-text-secondary text-center">
            {isInvite
              ? "Set a password and fill in your details to activate your account."
              : "Set up your account. You can change any of these later in Settings."}
          </p>
        </div>

        <div className="space-y-3">
          <Field label="Username" id="onb-username" required>
            {isInvite ? (
              <div
                id="onb-username"
                className="onb-input opacity-60 cursor-not-allowed"
                aria-readonly="true"
                role="textbox"
              >
                {username}
              </div>
            ) : (
              <input
                id="onb-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value.replace(/\s+/g, "").toLowerCase())}
                autoComplete="username"
                autoFocus
                placeholder="jay"
                className="onb-input"
              />
            )}
          </Field>

          <Field label="Full name" id="onb-fullname" required>
            <input
              id="onb-fullname"
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
              autoFocus={isInvite}
              placeholder="Jay Doe"
              className="onb-input"
            />
          </Field>

          <Field label="Email" id="onb-email" hint="Used for cloud services later. Optional today.">
            <input
              id="onb-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="you@example.com"
              className="onb-input"
            />
          </Field>

          <Field label="Password" id="onb-password" required hint="At least 4 characters.">
            <input
              id="onb-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className="onb-input"
            />
          </Field>

          <Field label="Confirm password" id="onb-confirm" required>
            <input
              id="onb-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              className="onb-input"
              aria-invalid={confirm.length > 0 && !matches}
            />
            {confirm.length > 0 && !matches && (
              <p className="text-[11px] text-red-400 mt-1">Passwords don't match.</p>
            )}
          </Field>

          <label
            htmlFor="onb-autologin"
            className="flex items-start gap-3 mt-1 cursor-pointer select-none"
          >
            <input
              id="onb-autologin"
              type="checkbox"
              checked={autoLogin}
              onChange={(e) => setAutoLogin(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-accent cursor-pointer"
            />
            <span className="text-xs text-shell-text-secondary leading-snug">
              Stay signed in on this device
              <span className="block text-[10px] text-shell-text-tertiary mt-0.5">
                Skips the login screen for a year. Turn off if this is a shared device.
              </span>
            </span>
          </label>
        </div>

        {error && (
          <p className="text-xs text-red-400 mt-3 text-center" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!valid || loading}
          className="w-full mt-5 px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {loading
            ? isInvite ? "Activating..." : "Setting up..."
            : isInvite ? "Activate account" : "Get started"}
        </button>

        <style>{`
          .onb-input {
            width: 100%;
            padding: 10px 14px;
            border-radius: 8px;
            background: var(--color-shell-bg-deep);
            border: 1px solid rgba(255, 255, 255, 0.10);
            color: var(--color-shell-text);
            font-size: 13px;
            outline: none;
            transition: border-color 0.15s;
          }
          .onb-input:focus {
            border-color: rgba(139, 146, 163, 0.45);
          }
        `}</style>
      </form>
    </div>
  );
}

/**
 * The free taOS username step (slice 5). The username is free and never gated
 * behind taOSgo, so the user can always finish via "Finish" even if the claim
 * fails or they skip it. Public subdomain publishing is a taOSgo perk and is
 * intentionally deferred to Settings, not offered here.
 */
function UsernameStep({
  defaultName,
  onDone,
}: {
  defaultName: string;
  onDone: () => void;
}) {
  const [name, setName] = useState(defaultName);
  const [claiming, setClaiming] = useState(false);
  const [claimed, setClaimed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function claim() {
    const trimmed = name.trim();
    if (!trimmed || claiming) return;
    setClaiming(true);
    setError(null);
    try {
      const res = await fetch("/api/account/username", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username: trimmed }),
      });
      if (res.ok) {
        setClaimed(true);
      } else {
        // Degrade to a non-blocking message; the user finishes in Settings.
        setError("We couldn't save that username right now. You can claim it later in Settings.");
      }
    } catch {
      setError("We couldn't reach the account service. You can claim your username later in Settings.");
    } finally {
      setClaiming(false);
    }
  }

  return (
    <div
      className="h-screen w-screen flex items-center justify-center overflow-y-auto"
      style={{
        background: "var(--color-shell-bg)",
        paddingTop: "calc(env(safe-area-inset-top, 0px) + 16px)",
        paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 16px)",
        paddingLeft: "calc(env(safe-area-inset-left, 0px) + 16px)",
        paddingRight: "calc(env(safe-area-inset-right, 0px) + 16px)",
      }}
    >
      <div
        className="w-full max-w-md p-6 rounded-2xl border border-white/10"
        style={{
          backgroundColor: "rgba(255,255,255,0.04)",
          backdropFilter: "blur(20px)",
        }}
        aria-label="Claim your free taOS username"
      >
        <div className="flex flex-col items-center gap-3 mb-6">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
          >
            <AtSign size={24} className="text-white" />
          </div>
          <h1 className="text-lg font-semibold text-shell-text">Claim your free taOS username</h1>
          <p className="text-xs text-shell-text-secondary text-center">
            Your username is free. It is your identity across taOS, so people can find you in the
            community, the apps you share, and your profile.
          </p>
        </div>

        <div className="space-y-3">
          <Field label="Username" id="onb-cloud-username" hint="Free. No subscription needed.">
            <div className="flex items-center gap-2">
              <span className="text-sm text-shell-text-tertiary select-none">@</span>
              <input
                id="onb-cloud-username"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value.replace(/\s+/g, "").toLowerCase())}
                autoFocus
                placeholder="jay"
                aria-label="taOS username"
                className="onb-input"
              />
            </div>
          </Field>

          {claimed && (
            <p className="text-xs text-emerald-400 flex items-center gap-1.5" role="status">
              <Check size={12} /> You're @{name.trim()} on taOS.
            </p>
          )}

          {error && (
            <p className="text-xs text-amber-400 flex items-center gap-1.5" role="alert">
              <AtSign size={12} /> {error}
            </p>
          )}

          <button
            type="button"
            onClick={() => void claim()}
            disabled={claiming || claimed || name.trim().length === 0}
            className="w-full px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {claiming ? "Claiming..." : claimed ? "Claimed" : "Claim username"}
          </button>

          <button
            type="button"
            onClick={onDone}
            className="w-full px-4 py-2.5 rounded-lg border border-white/10 text-shell-text-secondary text-sm font-medium hover:brightness-110 transition-all"
          >
            Finish
          </button>

          <p className="text-[11px] text-shell-text-tertiary text-center mt-2">
            Claiming is optional. Public subdomains are set up later in Settings.
          </p>
        </div>

        <style>{`
          .onb-input {
            width: 100%;
            padding: 10px 14px;
            border-radius: 8px;
            background: var(--color-shell-bg-deep);
            border: 1px solid rgba(255, 255, 255, 0.10);
            color: var(--color-shell-text);
            font-size: 13px;
            outline: none;
            transition: border-color 0.15s;
          }
          .onb-input:focus {
            border-color: rgba(139, 146, 163, 0.45);
          }
        `}</style>
      </div>
    </div>
  );
}

function Field({
  label,
  id,
  required,
  hint,
  children,
}: {
  label: string;
  id: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1"
      >
        {label}
        {required && <span className="text-red-400 ml-1" aria-hidden="true">*</span>}
      </label>
      {children}
      {hint && <p className="text-[10px] text-shell-text-tertiary mt-1">{hint}</p>}
    </div>
  );
}
