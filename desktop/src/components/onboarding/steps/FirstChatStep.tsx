import { MessageCircle } from "lucide-react";
import { OobePage } from "../OobeShell";
import { assistantNameError } from "../first-chat";

/**
 * Names the personal assistant built from "About you" and previews what the
 * first chat offers to ask. Continue sets it up and finishes setup.
 */
export function FirstChatStep({
  name,
  prompts,
  busy,
  error,
  onName,
  onBack,
  onNext,
  onSkip,
}: {
  name: string;
  prompts: string[];
  busy: boolean;
  error: string;
  onName: (v: string) => void;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const invalid = assistantNameError(name);
  return (
    <OobePage
      icon={<MessageCircle size={56} strokeWidth={1.5} aria-hidden="true" className="oobe-icon" />}
      title="Meet your assistant"
      subtitle="It starts with what you told taOS about yourself. Your first chat opens in Messages while it gets ready."
      onBack={busy ? undefined : onBack}
      onSubmit={() => !invalid && !busy && onNext()}
      footer={
        <>
          <button type="submit" className="oobe-primary" disabled={Boolean(invalid) || busy} aria-busy={busy}>
            {busy ? "Setting up..." : "Continue"}
          </button>
          <button type="button" className="oobe-secondary" onClick={onSkip} disabled={busy}>
            Set Up Later
          </button>
        </>
      }
    >
      <div className="oobe-group">
        <div className="oobe-row">
          <label htmlFor="oobe-assistant-name">Name</label>
          <input
            id="oobe-assistant-name"
            className="oobe-input"
            type="text"
            value={name}
            onChange={(e) => onName(e.target.value)}
            autoCapitalize="words"
            autoComplete="off"
            maxLength={64}
            disabled={busy}
            aria-invalid={Boolean(invalid)}
          />
        </div>
      </div>
      {invalid && name.trim() !== "" && <p className="oobe-note">{invalid}</p>}

      <section className="oobe-section">
        <p className="oobe-section-label">Try asking</p>
        <ul className="oobe-facts">
          {prompts.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </section>

      {error && (
        <p className="oobe-error" role="alert">
          {error}
        </p>
      )}
    </OobePage>
  );
}
