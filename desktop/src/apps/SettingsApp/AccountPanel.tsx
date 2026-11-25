import { useState, useEffect, useCallback, useRef, type FormEvent } from "react";
import { LogOut, AlertCircle, Plane, UserCircle, AtSign, Loader2, KeyRound } from "lucide-react";
import { Button, Card, Input, Label, Tabs, TabsList, TabsTrigger } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";
import { CountrySelect } from "@/components/CountrySelect";
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
/*  Country                                                           */
/* ------------------------------------------------------------------ */

/** The person's country. Some model licences do not apply in every country,
 *  and the Store only lets one of those install once it knows yours. Set by
 *  the person here, or when the Store asks at install time; never guessed
 *  from the network address. */
function CountryCard() {
  const [username, setUsername] = useState<string | null>(null);
  const [country, setCountry] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/auth/status", { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        if (mounted.current && data?.authenticated && data?.user) {
          setUsername(String(data.user.username ?? ""));
          const c = String(data.user.country ?? "");
          setCountry(c);
          setSaved(c);
        }
      } catch {
        // Host unreachable; the cloud card surfaces connectivity.
      }
    })();
  }, []);

  if (!username) return null;

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setStatus("");
    try {
      const res = await fetch(`/auth/users/${encodeURIComponent(username)}/profile`, withCsrf({
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ country }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || !body?.ok) {
        setError(body?.error || "Could not save your country.");
        return;
      }
      setSaved(country);
      setStatus(country ? "Country saved." : "Country cleared.");
    } catch {
      if (mounted.current) setError("Could not reach taOS. Check the connection and try again.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <Card className="p-4 mb-3">
      <Label htmlFor="account-country" className="text-sm font-medium">Country</Label>
      <p className="text-xs text-shell-text-tertiary mt-1 mb-3">
        Some model licences only apply in certain countries. The Store checks this
        before installing one. It is never guessed from your network address.
      </p>
      <CountrySelect id="account-country" value={country} onChange={setCountry} disabled={busy} />
      {error && (
        <p className="text-xs text-amber-400 flex items-center gap-1.5 mt-3" role="alert">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      <p aria-live="polite" className="sr-only">{status}</p>
      {status && <p className="text-xs text-emerald-400 mt-3">{status}</p>}
      <div className="mt-3">
        <Button type="button" onClick={save} disabled={busy || country === saved}>
          {busy ? "Saving..." : "Save country"}
        </Button>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Sign-in method (PIN)                                              */
/* ------------------------------------------------------------------ */

const PIN_MIN = 4;
const PIN_MAX = 12;

/** Set, change or turn off the PIN used to sign in on this device's own
 *  screen.
 *
 *  A PIN exists for the touchscreen case: a wall panel or pi-top has no
 *  keyboard, so a typed password locks the owner out of their own machine.
 *  It is deliberately NOT a general credential — the server refuses PIN
 *  sign-in from anywhere but the console — so this card says where it works
 *  rather than leaving the user to discover it does nothing over the network.
 *
 *  Setting one costs the account password even though the caller already holds
 *  a session: a PIN is a lasting way back into the device, so minting one must
 *  cost the real credential. Turning it off costs nothing, because removing a
 *  credential only ever reduces what an attacker can reach — and demanding a
 *  typed password to disable it would be unperformable on the very device this
 *  feature serves. */
function SignInMethodCard() {
  const [hasPin, setHasPin] = useState<boolean | null>(null);
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/auth/status", { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        if (mounted.current && data?.authenticated && data?.user) {
          setHasPin(Boolean(data.user.has_pin));
        }
      } catch {
        // Host unreachable; the cloud card surfaces connectivity.
      }
    })();
  }, []);

  const reset = () => { setPin(""); setConfirm(""); setPassword(""); setError(null); };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    // Checked here as well as on the server so the user is told which field is
    // wrong instead of being handed one generic refusal.
    if (!/^\d+$/.test(pin) || pin.length < PIN_MIN || pin.length > PIN_MAX) {
      setError(`A PIN must be ${PIN_MIN}-${PIN_MAX} digits.`);
      return;
    }
    if (pin !== confirm) { setError("The two PINs do not match."); return; }
    if (!password) { setError("Enter your account password to confirm."); return; }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/auth/pin", withCsrf({
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin, password }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || !body?.ok) {
        setError(body?.error || "Could not save the PIN.");
        return;
      }
      setHasPin(true);
      setOpen(false);
      reset();
      setStatus("PIN saved. You can now sign in with it on this screen.");
    } catch {
      if (mounted.current) setError("Could not reach taOS. Check the connection and try again.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const disable = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/auth/pin", withCsrf({ method: "DELETE", credentials: "include" }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || !body?.ok) {
        setError(body?.error || "Could not turn off the PIN.");
        return;
      }
      setHasPin(false);
      setOpen(false);
      reset();
      setStatus("PIN turned off. Sign in with your password.");
    } catch {
      if (mounted.current) setError("Could not reach taOS. Check the connection and try again.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  if (hasPin === null) return null;

  return (
    <Card className="p-4 mb-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <KeyRound size={16} className="text-shell-text-secondary" />
          <h3 className="text-sm font-medium">PIN sign-in</h3>
        </div>
        <span className={`text-xs font-medium ${hasPin ? "text-emerald-400" : "text-shell-text-tertiary"}`}>
          {hasPin ? "On" : "Off"}
        </span>
      </div>
        <p className="text-xs text-shell-text-tertiary mb-3">
          Sign in with {PIN_MIN}-{PIN_MAX} digits on this device's own screen, the
          quickest way in on a touchscreen with no keyboard. It works only on that
          screen; over the network your password is still required.
        </p>

      {/* Announced to screen readers without stealing focus. */}
      <p aria-live="polite" className="sr-only">{status}</p>
      {status && !open && (
        <p className="text-xs text-emerald-400 mb-3">{status}</p>
      )}
      {/* "Turn off" only exists in the COLLAPSED state, and disable() is the
          one action that can fail from here. The form below renders `error`
          too, but that branch is unreachable while the button is on screen --
          without this the tap fails, the spinner returns to the label, and
          nothing tells the user the PIN is still on. */}
      {error && !open && (
        <p className="text-xs text-amber-400 flex items-center gap-1.5 mb-3" role="alert">
          <AlertCircle size={12} /> {error}
        </p>
      )}

      {!open ? (
        <div className="flex gap-2">
          <Button size="sm" variant={hasPin ? "outline" : "default"}
                  onClick={() => { setStatus(""); reset(); setOpen(true); }}>
            {hasPin ? "Change PIN" : "Set up a PIN"}
          </Button>
          {hasPin && (
            <Button size="sm" variant="outline" onClick={disable} disabled={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : "Turn off"}
            </Button>
          )}
        </div>
      ) : (
        <form onSubmit={save} className="space-y-3">
          <div>
            <Label htmlFor="pin-new">New PIN</Label>
            <Input id="pin-new" type="password" inputMode="numeric" autoComplete="off"
                   maxLength={PIN_MAX} value={pin}
                   onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
          </div>
          <div>
            <Label htmlFor="pin-confirm">Confirm PIN</Label>
            <Input id="pin-confirm" type="password" inputMode="numeric" autoComplete="off"
                   maxLength={PIN_MAX} value={confirm}
                   onChange={(e) => setConfirm(e.target.value.replace(/\D/g, ""))} />
          </div>
          <div>
            <Label htmlFor="pin-password">Account password</Label>
            <Input id="pin-password" type="password" autoComplete="current-password"
                   value={password} onChange={(e) => setPassword(e.target.value)} />
            <p className="text-xs text-shell-text-tertiary mt-1">
              Confirms it is you setting a new way into this device.
            </p>
          </div>
          {error && (
            <p className="text-xs text-amber-400 flex items-center gap-1.5" role="alert">
              <AlertCircle size={12} /> {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : "Save PIN"}
            </Button>
            <Button type="button" size="sm" variant="outline"
                    onClick={() => { setOpen(false); reset(); }} disabled={busy}>
              Cancel
            </Button>
          </div>
        </form>
      )}
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
/*  Free username card (social identity, no taOSgo, no .taos.my)      */
/* ------------------------------------------------------------------ */

function UsernameCard({ account }: { account: Account }) {
  const { username } = account;

  if (username) {
    return (
      <Card className="p-4">
        <div className="flex items-center gap-2 mb-2">
          <AtSign size={16} className="text-sky-400" />
          <h3 className="text-sm font-medium">Your taOS username</h3>
        </div>
        <p className="text-sm font-medium">@{username}</p>
        <p className="text-xs text-shell-text-tertiary mt-0.5">
          Your free identity across taOS. People use @username to find you in the community, the
          apps you share, and your profile.
        </p>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-2">
        <AtSign size={16} className="text-sky-400" />
        <h3 className="text-sm font-medium">Claim your free taOS username</h3>
      </div>
      <p className="text-xs text-shell-text-tertiary mb-3">
        Your username is your free identity on taOS. It is yours at no cost, and it is what people
        use to find you across the community, the apps you share, and your profile.
      </p>
      <Button
        size="sm"
        onClick={() => { window.location.href = "https://taos.my/account/username"; }}
      >
        Claim your username
      </Button>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Signed-in view                                                    */
/* ------------------------------------------------------------------ */

function SignedIn({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  const username = account.username;
  const localPart = account.email.split("@")[0];
  const name = username ?? localPart ?? "taOS account";

  return (
    <div className="space-y-3">
      <Card className="p-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">{name}</p>
          {username && <p className="text-xs text-shell-text-secondary mt-0.5 truncate">@{username}</p>}
          <p className="text-xs text-shell-text-tertiary mt-0.5 truncate">{account.email}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onSignOut} aria-label="Disconnect">
          <LogOut size={14} /> Disconnect
        </Button>
      </Card>
      <TaosgoCard account={account} />
      <UsernameCard account={account} />
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
      <Tabs value={mode} onValueChange={(raw) => { setMode(raw as "signin" | "register"); setError(null); }}>
        <TabsList className="flex gap-2 mb-4" aria-label="Account action">
          <TabsTrigger value="signin">Sign in</TabsTrigger>
          <TabsTrigger value="register">Create account</TabsTrigger>
        </TabsList>
      </Tabs>

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
        Connecting lets your devices share one account.
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

      {/* This device */}
      <h3 className="text-sm font-semibold text-shell-text-secondary uppercase tracking-wider mb-3">This device</h3>
      <LocalAccountCard />
      <SignInMethodCard />
      <CountryCard />

      {/* taOS account */}
      <h3 className="text-sm font-semibold text-shell-text-secondary uppercase tracking-wider mb-3">taOS account</h3>
      <p className="text-xs text-shell-text-tertiary mb-5">
        Your taOS account is your identity across your taOS devices. taOSgo adds secure access from outside your home network.
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
        <>
          <p className="text-sm font-medium mb-3">Not connected</p>
          <SignedOut onSignedIn={(account) => setState({ kind: "signed-in", account })} />
        </>
      )}

      {state.kind === "signed-in" && (
        <SignedIn account={state.account} onSignOut={handleSignOut} />
      )}
    </section>
  );
}
