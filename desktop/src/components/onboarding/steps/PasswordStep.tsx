import { useState } from "react";
import { OobePage } from "../OobeShell";

export const MIN_PASSWORD = 8;

export function PasswordStep({
  password,
  confirm,
  autoLogin,
  invite,
  busy,
  error,
  onPassword,
  onConfirm,
  onAutoLogin,
  onBack,
  onSubmit,
}: {
  password: string;
  confirm: string;
  autoLogin: boolean;
  invite: boolean;
  busy: boolean;
  error: string;
  onPassword: (v: string) => void;
  onConfirm: (v: string) => void;
  onAutoLogin: (v: boolean) => void;
  onBack?: () => void;
  onSubmit: () => void;
}) {
  // Judge the confirmation only once the user has left it or typed as much
  // as the password, so it does not shout "don't match" at the first key.
  const [confirmTouched, setConfirmTouched] = useState(false);
  const longEnough = password.length >= MIN_PASSWORD;
  const matches = password.length > 0 && password === confirm;
  const showMismatch =
    confirm.length > 0 && !matches && (confirmTouched || confirm.length >= password.length);
  const valid = longEnough && matches;

  return (
    <OobePage
      title="Create a password"
      subtitle="You'll use it to sign in to taOS from other devices and to approve changes."
      onBack={busy ? undefined : onBack}
      onSubmit={() => valid && !busy && onSubmit()}
      footer={
        <button type="submit" className="oobe-primary" disabled={!valid || busy}>
          {busy
            ? invite ? "Activating..." : "Setting up..."
            : invite ? "Activate account" : "Continue"}
        </button>
      }
    >
      <section className="oobe-section">
        <div className="oobe-group">
          <div className="oobe-row">
            <label htmlFor="oobe-password">Password</label>
            <input
              id="oobe-password"
              className="oobe-input"
              type="password"
              value={password}
              onChange={(e) => onPassword(e.target.value)}
              autoComplete="new-password"
              placeholder="Required"
              aria-describedby="oobe-password-note"
              autoFocus
            />
          </div>
          <div className="oobe-row">
            <label htmlFor="oobe-confirm">Verify</label>
            <input
              id="oobe-confirm"
              aria-label="Verify password"
              className="oobe-input"
              type="password"
              value={confirm}
              onChange={(e) => onConfirm(e.target.value)}
              onBlur={() => setConfirmTouched(true)}
              autoComplete="new-password"
              placeholder="Required"
              aria-invalid={showMismatch}
            />
          </div>
        </div>
        {showMismatch ? (
          <p className="oobe-error" role="alert">Passwords don't match.</p>
        ) : (
          <p className="oobe-note" id="oobe-password-note">
            At least {MIN_PASSWORD} characters.
          </p>
        )}
      </section>

      <section className="oobe-section">
        <div className="oobe-group">
          <label className="oobe-switch-row" htmlFor="oobe-autologin">
            Stay signed in
            <input
              id="oobe-autologin"
              className="oobe-switch"
              type="checkbox"
              role="switch"
              checked={autoLogin}
              onChange={(e) => onAutoLogin(e.target.checked)}
            />
          </label>
        </div>
        <p className="oobe-note">
          Skips the sign-in screen on this device for a year. Turn it off on a shared device.
        </p>
      </section>

      {error && (
        <p className="oobe-error" role="alert">
          {error}
        </p>
      )}
    </OobePage>
  );
}
