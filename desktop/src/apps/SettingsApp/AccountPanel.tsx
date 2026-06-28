import { useState, useEffect, useCallback, useRef, type FormEvent } from "react";
import { LogOut, AlertCircle, ShieldCheck, Plane, UserCircle } from "lucide-react";
import { Button, Card, Input, Label } from "@/components/ui";
import {
  fetchAccount,
  login,
  register,
  logout,
  isAuthError,
  type AccountState,
  type Account,
  type TaosgoStatus,
} from "@/lib/account-client";

const TAOSGO_STATUS: Record<TaosgoStatus, { label: string; tone: string }> = {
  none: { label: "Not subscribed", tone: "text-shell-text-tertiary" },
  trialing: { label: "Free trial", tone: "text-sky-400" },
  active: { label: "Active", tone: "text-emerald-400" },
  past_due: { label: "Payment due", tone: "text-amber-400" },
};

function formatDate(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/* ------------------------------------------------------------------ */
/*  This-device (local) identity                                      */
/* ------------------------------------------------------------------ */

interface LocalUser {
  username?: string;
  full_name?: string;
  email?: string;
  is_admin?: boolean;
}

/** The account you are signed into on THIS device (the controller's own user),
 *  read from /auth/status. Distinct from the taos.my cloud account below, and
 *  shown even when the cloud account is unreachable so you always know who you
 *  are signed in as. Renders nothing when signed out or the host is unreachable
 *  (the cloud card already surfaces connectivity). */
function LocalAccountCard() {
  const [user, setUser] = useState<LocalUser | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/auth/status", { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        if (mounted.current && data?.authenticated && data?.user) setUser(data.user);
      } catch {
        // Host unreachable; the cloud card surfaces connectivity, so stay quiet.
      }
    })();
  }, []);

  if (!user) return null;
  const name = user.full_name?.trim() || user.username || "Signed in";
  const meta = [
    user.username ? `@${user.username}` : null,
    user.email || null,
    user.is_admin ? "Admin" : null,
  ].filter(Boolean).join(" · ");

  return (
    <Card className="p-4 mb-3 flex items-center gap-3">
      <UserCircle size={20} className="text-shell-text-secondary shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-medium truncate">{name}</p>
        {meta && <p className="text-xs text-shell-text-tertiary mt-0.5 truncate">{meta}</p>}
        <p className="text-xs text-shell-text-tertiary mt-0.5">Signed in on this device</p>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  taOSgo subscription card                                          */
/* ------------------------------------------------------------------ */

function TaosgoCard({ account }: { account: Account }) {
  const { status } = account.taosgo;
  const meta = TAOSGO_STATUS[status];
  const trialEnds = formatDate(account.taosgo.trial_ends_at);
  const renews = formatDate(account.taosgo.current_period_end);

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Plane size={16} className="text-sky-400" />
          <h3 className="text-sm font-medium">taOSgo</h3>
        </div>
        <span className={`text-xs font-medium ${meta.tone}`}>{meta.label}</span>
      </div>
      <p className="text-xs text-shell-text-tertiary mb-3">
        Secure access to your taOS from any browser, anywhere, with nothing to install.
      </p>
      {status === "trialing" && trialEnds && (
        <p className="text-xs text-shell-text-secondary mb-3">Trial ends {trialEnds}.</p>
      )}
      {status === "active" && renews && (
        <p className="text-xs text-shell-text-secondary mb-3">Renews {renews}.</p>
      )}
      {status === "none" ? (
        <Button size="sm" onClick={() => { window.location.href = "https://taos.my/taosgo"; }}>
          Start 7-day free trial
        </Button>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={() => { window.location.href = "https://taos.my/account/billing"; }}
        >
          Manage subscription
        </Button>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Signed-in view                                                    */
/* ------------------------------------------------------------------ */

function SignedIn({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  return (
    <div className="space-y-3">
      <Card className="p-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">{account.email}</p>
          <p className="text-xs text-shell-text-tertiary mt-0.5">Signed in to your taOS account</p>
        </div>
        <Button variant="outline" size="sm" onClick={onSignOut} aria-label="Sign out">
          <LogOut size={14} /> Sign out
        </Button>
      </Card>
      <TaosgoCard account={account} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Signed-out view (sign in / register)                              */
/* ------------------------------------------------------------------ */

function SignedOut({ onSignedIn }: { onSignedIn: (account: Account) => void }) {
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const result = await (mode === "signin" ? login : register)(email.trim(), password);
    if (!mounted.current) return;
    setBusy(false);
    if (isAuthError(result)) setError(result.message);
    else onSignedIn(result);
  };

  return (
    <Card className="p-4">
      <div className="flex gap-2 mb-4" role="group" aria-label="Account action">
        <Button
          variant={mode === "signin" ? "secondary" : "outline"}
          size="sm"
          onClick={() => { setMode("signin"); setError(null); }}
          aria-pressed={mode === "signin"}
        >
          Sign in
        </Button>
        <Button
          variant={mode === "register" ? "secondary" : "outline"}
          size="sm"
          onClick={() => { setMode("register"); setError(null); }}
          aria-pressed={mode === "register"}
        >
          Create account
        </Button>
      </div>

      <form onSubmit={submit} className="space-y-3">
        <div>
          <Label htmlFor="account-email" className="text-xs text-shell-text-secondary">Email</Label>
          <Input
            id="account-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="mt-1"
          />
        </div>
        <div>
          <Label htmlFor="account-password" className="text-xs text-shell-text-secondary">Password</Label>
          <Input
            id="account-password"
            type="password"
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === "register" ? "At least 8 characters" : "Your password"}
            className="mt-1"
          />
        </div>

        {error && (
          <p className="text-xs text-amber-400 flex items-center gap-1.5" role="alert">
            <AlertCircle size={12} /> {error}
          </p>
        )}

        <Button type="submit" size="sm" disabled={busy} className="w-full">
          {busy ? "Please wait..." : mode === "signin" ? "Sign in" : "Create account"}
        </Button>
      </form>

      <p className="text-xs text-shell-text-tertiary mt-3 flex items-center gap-1.5">
        <ShieldCheck size={12} /> Your taOS account unlocks taOSgo and syncs across your devices.
      </p>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Account section                                                   */
/* ------------------------------------------------------------------ */

export function AccountSection() {
  const [state, setState] = useState<AccountState>({ kind: "loading" });
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const load = useCallback(async () => {
    const next = await fetchAccount();
    if (mounted.current) setState(next);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSignOut = async () => {
    await logout();
    if (mounted.current) setState({ kind: "signed-out" });
  };

  return (
    <section aria-label="Account">
      <h2 className="text-lg font-semibold mb-2">Account</h2>

      <LocalAccountCard />

      <p className="text-sm text-shell-text-tertiary mb-5">
        Sign in to your taOS cloud account to manage taOSgo and sync settings across your devices.
      </p>

      {state.kind === "loading" && (
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      )}

      {state.kind === "unavailable" && (
        <Card className="p-4">
          <p className="text-sm flex items-center gap-2">
            <AlertCircle size={14} className="text-amber-400" />
            The account service is not reachable right now.
          </p>
          <p className="text-xs text-shell-text-tertiary mt-1">
            Accounts run on taos.my; try again once you are connected.
          </p>
          <Button variant="outline" size="sm" onClick={load} className="mt-3">Retry</Button>
        </Card>
      )}

      {state.kind === "signed-out" && (
        <SignedOut onSignedIn={(account) => setState({ kind: "signed-in", account })} />
      )}

      {state.kind === "signed-in" && (
        <SignedIn account={state.account} onSignOut={handleSignOut} />
      )}
    </section>
  );
}
