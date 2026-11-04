import { Check } from "lucide-react";
import { AUTONOMY, HELP_TOPICS, TONES, type AboutYou, type HelpTopic } from "../about";
import { OobePage } from "../OobeShell";

/** Page 1: what the user wants help with. Any number, including none. */
export function AboutHelpStep({
  about,
  onChange,
  onBack,
  onNext,
  onSkip,
}: {
  about: AboutYou;
  onChange: (about: AboutYou) => void;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  function toggle(id: HelpTopic) {
    const help = about.help.includes(id) ? about.help.filter((h) => h !== id) : [...about.help, id];
    onChange({ ...about, help });
  }
  return (
    <OobePage
      title="What would you like help with?"
      subtitle="Pick as many as you like. Your personal assistant starts with these in mind."
      onBack={onBack}
      onSubmit={onNext}
      footer={
        <>
          <button type="submit" className="oobe-primary">
            Continue
          </button>
          <button type="button" className="oobe-secondary" onClick={onSkip}>
            Set Up Later
          </button>
        </>
      }
    >
      <div className="oobe-chips" role="group" aria-label="Help with">
        {HELP_TOPICS.map((t) => {
          const on = about.help.includes(t.id);
          return (
            <button
              key={t.id}
              type="button"
              className="oobe-chip"
              aria-pressed={on}
              onClick={() => toggle(t.id)}
            >
              {on && <Check size={16} aria-hidden="true" />}
              {t.label}
            </button>
          );
        })}
      </div>
    </OobePage>
  );
}

/** Page 2: how the assistant talks, and whether it asks before acting. */
export function AboutStyleStep({
  about,
  onChange,
  onBack,
  onNext,
}: {
  about: AboutYou;
  onChange: (about: AboutYou) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  return (
    <OobePage
      title="How should it work with you?"
      subtitle="You can change this any time in the assistant's settings."
      onBack={onBack}
      onSubmit={onNext}
      footer={
        <button type="submit" className="oobe-primary">
          Continue
        </button>
      }
    >
      <OptionGroup
        label="Tone"
        options={TONES}
        value={about.tone}
        onPick={(tone) => onChange({ ...about, tone })}
      />
      <OptionGroup
        label="Taking action"
        options={AUTONOMY}
        value={about.autonomy}
        onPick={(autonomy) => onChange({ ...about, autonomy })}
      />
    </OobePage>
  );
}

function OptionGroup<T extends string>({
  label,
  options,
  value,
  onPick,
}: {
  label: string;
  options: { id: T; label: string; detail: string }[];
  value: T;
  onPick: (id: T) => void;
}) {
  return (
    <div className="oobe-section">
      <p className="oobe-section-label" id={`oobe-opt-${label}`}>
        {label}
      </p>
      <div className="oobe-group" role="radiogroup" aria-labelledby={`oobe-opt-${label}`}>
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={value === o.id}
            className="oobe-choice"
            onClick={() => onPick(o.id)}
          >
            <span className="oobe-choice-text">
              <span className="oobe-choice-title">{o.label}</span>
              <span className="oobe-choice-detail">{o.detail}</span>
            </span>
            <Check
              size={20}
              aria-hidden="true"
              className="oobe-choice-check"
              style={{ visibility: value === o.id ? "visible" : "hidden" }}
            />
          </button>
        ))}
      </div>
    </div>
  );
}
