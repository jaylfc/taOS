import { useRef, useState } from "react";
import { OobeShell } from "./OobeShell";
import { saveProgress, type OnboardingProgress } from "./progress";
import { HelloStep } from "./steps/HelloStep";
import { NameStep, deriveUsername } from "./steps/NameStep";
import { PasswordStep } from "./steps/PasswordStep";
import { TaosAccountChoice, TaosAccountForm, type AccountMode } from "./steps/TaosAccountStep";

export type StepId = "hello" | "name" | "password" | "taos-account";

const FIRST_RUN: StepId[] = ["hello", "name", "password", "taos-account"];
const INVITE: StepId[] = ["name", "password"];
/** Steps the account must exist for; a resume can land on these only. */
const AFTER_ACCOUNT: StepId[] = ["taos-account"];

export interface OobeProps {
  onDone: () => void;
  /** Invite completion: the username is fixed and the account already exists. */
  invitedUsername?: string;
  inviteCode?: string;
  defaultAutoLogin?: boolean;
  /** Resume first-run setup here (the account was created in an earlier visit). */
  resume?: Pick<OnboardingProgress, "step" | "completed" | "skipped">;
}

export function Oobe({ onDone, invitedUsername, inviteCode, defaultAutoLogin, resume }: OobeProps) {
  // /auth/status never returns the invite code, so the username alone marks
  // invite mode; the server accepts the invitee's pending session instead.
  const invite = Boolean(invitedUsername);
  const steps = invite ? INVITE : FIRST_RUN;
  // A resume always lands after account creation: an unknown step (from a
  // newer or older build) goes to the first such step, never back to /auth/setup.
  const resumeAt: StepId | null = resume
    ? (AFTER_ACCOUNT as string[]).includes(resume.step)
      ? (resume.step as StepId)
      : AFTER_ACCOUNT[0]!
    : null;

  const [step, setStep] = useState<StepId>(resumeAt ?? steps[0]!);
  const [direction, setDirection] = useState<1 | -1>(1);
  const [accountMode, setAccountMode] = useState<AccountMode | null>(null);

  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState(invitedUsername ?? "");
  const usernameEdited = useRef(invite);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [autoLogin, setAutoLogin] = useState(defaultAutoLogin ?? !invite);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const done = useRef<{ completed: string[]; skipped: string[] }>({
    completed: resume?.completed ?? [],
    skipped: resume?.skipped ?? [],
  });

  function go(next: StepId, dir: 1 | -1) {
    setDirection(dir);
    setStep(next);
  }

  function back() {
    const i = steps.indexOf(step);
    if (i > 0) go(steps[i - 1]!, -1);
  }

  /** Leave `step` (completed or skipped) for the next one, or finish. */
  async function advance(outcome: "completed" | "skipped") {
    const log = done.current;
    if (!log[outcome].includes(step)) log[outcome] = [...log[outcome], step];
    const next = steps[steps.indexOf(step) + 1];
    // Progress is the owner's preference: writable only once /auth/setup has
    // signed this browser in. Earlier, a PUT would 401 and read as an expired
    // session.
    const signedIn = !invite && steps.indexOf(step) >= steps.indexOf("password");
    if (next) {
      if (signedIn) void saveProgress({ in_progress: true, step: next, ...log });
      go(next, 1);
      return;
    }
    // Awaited: LoginGate re-reads progress in onDone and must not see
    // in_progress still true, or it would send the user straight back here.
    await saveProgress({ in_progress: false, step: "done", ...log });
    onDone();
  }

  function onFullName(v: string) {
    setFullName(v);
    if (!usernameEdited.current) setUsername(deriveUsername(v));
  }

  function onUsername(v: string) {
    usernameEdited.current = v.length > 0;
    setUsername(v);
  }

  async function createAccount() {
    setBusy(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        username: username.trim(),
        full_name: fullName.trim(),
        email: "",
        password,
        auto_login: autoLogin,
      };
      if (invite && inviteCode) body.invite_code = inviteCode;
      const res = await fetch(invite ? "/auth/complete" : "/auth/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? data?.detail ?? `Setup failed (${res.status})`);
        setBusy(false);
        return;
      }
    } catch {
      setError("Couldn't reach taOS. Check the connection and try again.");
      setBusy(false);
      return;
    }
    setBusy(false);
    if (invite) {
      onDone();
      return;
    }
    await advance("completed");
  }

  let page;
  switch (step) {
    case "hello":
      page = <HelloStep onNext={() => void advance("completed")} />;
      break;
    case "name":
      page = (
        <NameStep
          fullName={fullName}
          username={username}
          invite={invite}
          onFullName={onFullName}
          onUsername={onUsername}
          onBack={invite ? undefined : back}
          onNext={() => go("password", 1)}
        />
      );
      break;
    case "password":
      page = (
        <PasswordStep
          password={password}
          confirm={confirm}
          autoLogin={autoLogin}
          invite={invite}
          busy={busy}
          error={error}
          onPassword={setPassword}
          onConfirm={setConfirm}
          onAutoLogin={setAutoLogin}
          onBack={back}
          onSubmit={() => void createAccount()}
        />
      );
      break;
    case "taos-account":
      // No Back on the choice: the local account exists now, and the screens
      // before this would only create it again.
      page = accountMode ? (
        <TaosAccountForm
          mode={accountMode}
          onDone={() => void advance("completed")}
          onOffline={() => void advance("skipped")}
          onBack={() => {
            setDirection(-1);
            setAccountMode(null);
          }}
        />
      ) : (
        <TaosAccountChoice
          onChoose={(mode) => {
            setDirection(1);
            setAccountMode(mode);
          }}
          onOffline={() => void advance("skipped")}
        />
      );
      break;
  }

  return (
    <OobeShell stepKey={accountMode && step === "taos-account" ? `${step}:${accountMode}` : step} direction={direction}>
      {page}
    </OobeShell>
  );
}
