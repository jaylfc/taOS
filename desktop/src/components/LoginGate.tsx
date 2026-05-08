import { useState, useEffect, useCallback } from "react";
import { Lock } from "lucide-react";
import { OnboardingScreen } from "./OnboardingScreen";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth-guard";

interface Props {
  children: React.ReactNode;
}

type AuthStatus =
  | { phase: "loading" }
  | { phase: "onboarding" }
  | { phase: "invite"; username: string; inviteCode: string; multiUser: boolean }
  | { phase: "login"; legacy: boolean; multiUser: boolean }
  | { phase: "ready" };

export function LoginGate({ children }: Props) {
  const [status, setStatus] = useState<AuthStatus>({ phase: "loading" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [autoLogin, setAutoLogin] = useState(true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch("/auth/status", { credentials: "include" });
      if (!res.ok) {
        setStatus({ phase: "ready" });
        return;
      }
      const data = await res.json();
      if (!data.configured) {
        setStatus({ phase: "onboarding" });
      } else if (data.authenticated) {
        if (data.needs_onboarding && data.user?.username) {
          // Pending invited user — collect their profile and password
          setStatus({
            phase: "invite",
            username: data.user.username,
            inviteCode: "",   // invite code was accepted at login; the session holds it
            multiUser: !!data.multi_user,
          });
        } else {
          setStatus({ phase: "ready" });
        }
      } else {
        setStatus({ phase: "login", legacy: !data.user, multiUser: !!data.multi_user });
      }
    } catch {
      setStatus({ phase: "ready" });
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // Listen for session-expired events from the global auth guard. Any
  // /api/* call returning 401 fires this; we re-run /auth/status which
  // will flip phase to "login" and unmount the app shell back to the
  // sign-in form. Without this, a stale cookie (e.g. after a controller
  // reinstall) left every app rendering empty data with no signal to
  // re-authenticate.
  useEffect(() => {
    const onExpired = () => {
      // Only re-prompt if we currently think we're authenticated.
      // Avoids a refresh loop if the user is already on the login form.
      setStatus((cur) => (cur.phase === "ready" ? { phase: "loading" } : cur));
      void refreshStatus();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [refreshStatus]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          username: username.trim() || undefined,
          password,
          auto_login: autoLogin,
        }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        if (data.needs_onboarding && data.user?.username) {
          // Pending user — route to invite completion
          setStatus({
            phase: "invite",
            username: data.user.username,
            inviteCode: password,  // the invite code they just typed as password
            multiUser: true,
          });
        } else {
          await refreshStatus();
        }
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? "Incorrect username or password");
      }
    } catch {
      setError("Login failed");
    }
    setLoading(false);
  };

  if (status.phase === "loading") {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-shell-bg text-shell-text-tertiary text-sm">
        Loading...
      </div>
    );
  }

  if (status.phase === "onboarding") {
    return <OnboardingScreen onDone={refreshStatus} defaultAutoLogin={true} />;
  }

  if (status.phase === "invite") {
    return (
      <OnboardingScreen
        onDone={refreshStatus}
        invitedUsername={status.username}
        inviteCode={status.inviteCode}
        defaultAutoLogin={!status.multiUser}
      />
    );
  }

  if (status.phase === "login") {
    const showUsername = !status.legacy;
    // Default auto-login to false in multi-user mode
    const defaultAutoLogin = !status.multiUser;

    return (
      <div
        className="h-screen w-screen flex items-center justify-center p-4"
        style={{ background: "linear-gradient(160deg, #1a1b2e 0%, #1e2140 40%, #252848 100%)" }}
      >
        <form
          onSubmit={handleSubmit}
          className="w-full max-w-sm p-6 rounded-2xl border border-white/10"
          style={{
            backgroundColor: "rgba(255,255,255,0.04)",
            backdropFilter: "blur(20px)",
          }}
        >
          <div className="flex flex-col items-center gap-3 mb-6">
            <div
              className="w-14 h-14 rounded-2xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
            >
              <Lock size={24} className="text-white" />
            </div>
            <h1 className="text-lg font-semibold text-shell-text">taOS</h1>
            <p className="text-xs text-shell-text-secondary">Sign in to continue</p>
          </div>

          {showUsername && (
            <>
              <label htmlFor="login-username" className="sr-only">Username</label>
              <input
                id="login-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                placeholder="Username"
                className="w-full px-4 py-2.5 mb-2 rounded-lg bg-shell-bg-deep border border-white/10 text-sm text-shell-text outline-none focus:border-accent/40 transition-colors"
              />
            </>
          )}

          <label htmlFor="login-password" className="sr-only">Password</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus={!showUsername}
            placeholder={showUsername ? "Password or invite code" : "Password"}
            className="w-full px-4 py-2.5 rounded-lg bg-shell-bg-deep border border-white/10 text-sm text-shell-text outline-none focus:border-accent/40 transition-colors"
          />

          {error && <p className="text-xs text-red-400 mt-2" role="alert">{error}</p>}

          <label
            htmlFor="login-autologin"
            className="flex items-center gap-2 mt-3 cursor-pointer select-none"
          >
            <input
              id="login-autologin"
              type="checkbox"
              checked={autoLogin}
              onChange={(e) => setAutoLogin(e.target.checked)}
              defaultChecked={defaultAutoLogin}
              className="w-4 h-4 accent-accent cursor-pointer"
            />
            <span className="text-xs text-shell-text-secondary">Stay signed in on this device</span>
          </label>

          <button
            type="submit"
            disabled={loading || !password || (showUsername && !username)}
            className="w-full mt-4 px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 disabled:opacity-50 transition-all"
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
