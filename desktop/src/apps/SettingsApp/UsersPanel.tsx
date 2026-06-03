import { useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import {
  Check,
  Copy,
  Trash2,
  KeyRound,
  X,
  AlertCircle,
  Plus,
} from "lucide-react";
import {
  Button,
  Card,
  Input,
  Label,
  Switch,
} from "@/components/ui";
import { useServerPreference } from "@/hooks/use-server-preference";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface UserRecord {
  id: string;
  username: string;
  full_name: string;
  email: string;
  is_admin: boolean;
  pending: boolean;
  invite_code?: string;
  last_login_at?: number | null;
  created_at?: number | null;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1 text-xs text-shell-text-secondary hover:text-shell-text transition-colors"
      aria-label={label ?? `Copy ${text}`}
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div ref={ref} className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-sm shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">{title}</h3>
          <button onClick={onClose} className="text-shell-text-tertiary hover:text-shell-text" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function AddUserModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState<string | null>(null);

  const submit = async () => {
    if (!username.trim()) return;
    setLoading(true);
    setError("");
    const resp = await fetch("/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username: username.trim() }),
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to add user");
      return;
    }
    const d = await resp.json();
    setCode(d.invite_code);
    onAdded();
  };

  return (
    <Modal title="Add user" onClose={onClose}>
      {code ? (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">Share this invite code with the user. It is shown only once.</p>
          <div className="flex items-center justify-between rounded-lg bg-shell-bg-deep border border-white/10 px-4 py-3">
            <span className="font-mono text-lg tracking-widest text-shell-text">{code}</span>
            <CopyButton text={code} label="Copy invite code" />
          </div>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <Label htmlFor="new-username">Username</Label>
            <Input
              id="new-username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value.replace(/\s+/g, "").toLowerCase())}
              placeholder="alice"
              className="mt-1"
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            />
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={submit} disabled={loading || !username.trim()}>
              {loading ? "Adding..." : "Add"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ResetPasswordModal({ username, onClose, onReset }: { username: string; onClose: () => void; onReset: () => void }) {
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState("");

  const doReset = async () => {
    setLoading(true);
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}/reset`, {
      method: "POST",
      credentials: "include",
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to reset");
      return;
    }
    const d = await resp.json();
    setCode(d.invite_code);
    onReset();
  };

  return (
    <Modal title={`Reset password for ${username}`} onClose={onClose}>
      {code ? (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">New invite code for {username}:</p>
          <div className="flex items-center justify-between rounded-lg bg-shell-bg-deep border border-white/10 px-4 py-3">
            <span className="font-mono text-lg tracking-widest text-shell-text">{code}</span>
            <CopyButton text={code} label="Copy reset code" />
          </div>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">
            This will revoke {username}'s current password and sessions. A new invite code will be generated.
          </p>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={doReset} disabled={loading}>
              {loading ? "Resetting..." : "Reset password"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ChangePasswordModal({ username, onClose }: { username: string; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);

  const matches = next.length > 0 && next === confirm;
  const valid = current.length > 0 && next.length >= 4 && matches;

  const submit = async () => {
    if (!valid) return;
    setLoading(true);
    setError("");
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ current, new: next }),
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to change password");
      return;
    }
    setDone(true);
  };

  return (
    <Modal title="Change password" onClose={onClose}>
      {done ? (
        <div className="space-y-3">
          <p className="text-sm text-emerald-400 flex items-center gap-2"><Check size={14} /> Password changed.</p>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <Label htmlFor="pw-current">Current password</Label>
            <Input id="pw-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} className="mt-1" autoFocus />
          </div>
          <div>
            <Label htmlFor="pw-new">New password</Label>
            <Input id="pw-new" type="password" value={next} onChange={(e) => setNext(e.target.value)} className="mt-1" />
          </div>
          <div>
            <Label htmlFor="pw-confirm">Confirm</Label>
            <Input
              id="pw-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1"
              aria-invalid={confirm.length > 0 && !matches}
            />
            {confirm.length > 0 && !matches && <p className="text-[11px] text-red-400 mt-1">Passwords don't match.</p>}
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" disabled={!valid || loading} onClick={submit}>
              {loading ? "Saving..." : "Change password"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function DeleteUserModal({ username, onClose, onDeleted }: { username: string; onClose: () => void; onDeleted: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const doDelete = async () => {
    setLoading(true);
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}`, {
      method: "DELETE",
      credentials: "include",
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to remove user");
      return;
    }
    onDeleted();
    onClose();
  };

  return (
    <Modal title={`Remove ${username}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-shell-text-secondary">
          This will remove {username} and revoke all their sessions. This cannot be undone.
        </p>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={doDelete} disabled={loading}
            className="bg-red-500/20 text-red-300 hover:bg-red-500/30 border-red-500/30">
            {loading ? "Removing..." : "Remove user"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/*  UsersSection                                                       */
/* ------------------------------------------------------------------ */

export function UsersSection() {
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [currentUser, setCurrentUser] = useState<UserRecord | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [multiUser, setMultiUser] = useState(false);
  const [editFullName, setEditFullName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [profileSaved, setProfileSaved] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [showAddUser, setShowAddUser] = useState(false);
  const [resetTarget, setResetTarget] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const [autoLoginDefault, setAutoLoginDefault] = useServerPreference<boolean>(
    "auto-login",
    true,
    (blob) => typeof blob.value === "boolean" ? blob.value : true,
    (v) => ({ value: v }),
  );

  const loadData = useCallback(async () => {
    try {
      const statusResp = await fetch("/auth/status", { credentials: "include" });
      if (statusResp.ok) {
        const s = await statusResp.json();
        setMultiUser(!!s.multi_user);
        if (s.user) {
          const u = s.user as UserRecord;
          setCurrentUser(u);
          setIsAdmin(!!u.is_admin);
          setEditFullName(u.full_name ?? "");
          setEditEmail(u.email ?? "");
        }
      }
    } catch { /* ignore */ }
    try {
      const usersResp = await fetch("/auth/users", { credentials: "include" });
      if (usersResp.ok) {
        const d = await usersResp.json();
        setUsers(d.users ?? []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const saveProfile = async () => {
    if (!currentUser) return;
    setProfileError("");
    const resp = await fetch(`/auth/users/${encodeURIComponent(currentUser.username)}/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ full_name: editFullName, email: editEmail }),
    });
    if (resp.ok) {
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 2000);
      loadData();
    } else {
      const d = await resp.json().catch(() => ({}));
      setProfileError((d as { error?: string }).error ?? "Save failed");
    }
  };

  const formatDate = (ts?: number | null) =>
    ts ? new Date(ts * 1000).toLocaleDateString() : "—";

  return (
    <section aria-label="Users and account settings">
      <h2 className="text-lg font-semibold mb-5">Users</h2>

      {/* My Account card */}
      <Card className="p-5 mb-4 space-y-4">
        <h3 className="text-sm font-semibold">My Account</h3>

        <div>
          <Label htmlFor="acct-username" className="text-xs text-shell-text-tertiary mb-1 block">Username</Label>
          <div className="flex items-center gap-2">
            <Input
              id="acct-username"
              value={currentUser?.username ?? ""}
              readOnly
              className="opacity-60 cursor-not-allowed"
              aria-readonly="true"
            />
          </div>
          <p className="text-[10px] text-shell-text-tertiary mt-1">Username cannot be changed.</p>
        </div>

        <div>
          <Label htmlFor="acct-fullname" className="text-xs text-shell-text-tertiary mb-1 block">Full name</Label>
          <Input
            id="acct-fullname"
            value={editFullName}
            onChange={(e) => { setEditFullName(e.target.value); setProfileSaved(false); }}
            placeholder="Your name"
          />
        </div>

        <div>
          <Label htmlFor="acct-email" className="text-xs text-shell-text-tertiary mb-1 block">Email</Label>
          <Input
            id="acct-email"
            type="email"
            value={editEmail}
            onChange={(e) => { setEditEmail(e.target.value); setProfileSaved(false); }}
            placeholder="you@example.com"
          />
        </div>

        {profileError && (
          <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertCircle size={12} /> {profileError}</p>
        )}

        <div className="flex items-center gap-2">
          <Button size="sm" onClick={saveProfile}>
            {profileSaved ? <><Check size={12} /> Saved</> : "Save changes"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowChangePassword(true)}>
            <KeyRound size={14} /> Change password
          </Button>
        </div>

        <div className="border-t border-white/5 pt-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <Label htmlFor="acct-autologin" className="text-sm">Stay signed in by default on this device</Label>
              <p className="text-[11px] text-shell-text-tertiary mt-0.5">
                When on, the login form's "Stay signed in" checkbox starts checked.
              </p>
            </div>
            <Switch
              id="acct-autologin"
              checked={autoLoginDefault}
              onCheckedChange={setAutoLoginDefault}
              aria-label="Stay signed in by default"
            />
          </div>
        </div>
      </Card>

      {/* Team Members card — admin only */}
      {isAdmin && (
        <Card className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Team Members</h3>
            <Button size="sm" onClick={() => setShowAddUser(true)}>
              <Plus size={14} /> Add user
            </Button>
          </div>

          {multiUser && (
            <p className="text-xs text-shell-text-tertiary">
              Auto-login is disabled by default for new sessions while multiple users exist.
            </p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[500px]">
              <thead>
                <tr className="border-b border-white/[0.08]">
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Username</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Full name</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Email</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Last login</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Status</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.username} className="border-b border-white/5 last:border-0">
                    <td className="py-2.5 px-3 font-medium">{u.username}</td>
                    <td className="py-2.5 px-3 text-shell-text-secondary">{u.full_name || "—"}</td>
                    <td className="py-2.5 px-3 text-shell-text-secondary truncate max-w-[140px]">{u.email || "—"}</td>
                    <td className="py-2.5 px-3 text-shell-text-tertiary tabular-nums">{formatDate(u.last_login_at)}</td>
                    <td className="py-2.5 px-3">
                      {u.pending ? (
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-medium">pending</span>
                          {u.invite_code && (
                            <div className="flex items-center gap-1">
                              <span className="font-mono text-xs text-shell-text-secondary">{u.invite_code}</span>
                              <CopyButton text={u.invite_code} label={`Copy invite code for ${u.username}`} />
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-medium">active</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="flex items-center gap-1.5">
                        {u.username !== currentUser?.username && (
                          <>
                            <button
                              onClick={() => setResetTarget(u.username)}
                              className="p-1 rounded hover:bg-white/10 text-shell-text-tertiary hover:text-shell-text transition-colors"
                              aria-label={`Reset password for ${u.username}`}
                              title="Reset password"
                            >
                              <KeyRound size={13} />
                            </button>
                            <button
                              onClick={() => setDeleteTarget(u.username)}
                              className="p-1 rounded hover:bg-red-500/20 text-shell-text-tertiary hover:text-red-400 transition-colors"
                              aria-label={`Remove ${u.username}`}
                              title="Remove user"
                            >
                              <Trash2 size={13} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 px-3 text-sm text-shell-text-tertiary">No users yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {showChangePassword && currentUser && (
        <ChangePasswordModal
          username={currentUser.username}
          onClose={() => setShowChangePassword(false)}
        />
      )}
      {showAddUser && (
        <AddUserModal
          onClose={() => setShowAddUser(false)}
          onAdded={loadData}
        />
      )}
      {resetTarget && (
        <ResetPasswordModal
          username={resetTarget}
          onClose={() => setResetTarget(null)}
          onReset={loadData}
        />
      )}
      {deleteTarget && (
        <DeleteUserModal
          username={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={loadData}
        />
      )}
    </section>
  );
}
