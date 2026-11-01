import { OobePage } from "../OobeShell";

/** A sign-in name from a full name: "Ada Lovelace" -> "ada". */
export function deriveUsername(fullName: string): string {
  const first = fullName.trim().split(/\s+/)[0] ?? "";
  return first.toLowerCase().replace(/[^a-z0-9._-]/g, "");
}

export function cleanUsername(raw: string): string {
  return raw.replace(/\s+/g, "").toLowerCase();
}

export function NameStep({
  fullName,
  username,
  invite,
  onFullName,
  onUsername,
  onBack,
  onNext,
}: {
  fullName: string;
  username: string;
  /** Invite completion: the username was chosen by whoever invited them. */
  invite: boolean;
  onFullName: (v: string) => void;
  onUsername: (v: string) => void;
  onBack?: () => void;
  onNext: () => void;
}) {
  const valid = fullName.trim().length > 0 && username.trim().length > 0;
  return (
    <OobePage
      title={invite ? "Complete your account" : "What's your name?"}
      subtitle={
        invite
          ? "You've been invited to this taOS. Add your name, then choose a password."
          : "This is how taOS and your agents will address you."
      }
      onBack={onBack}
      onSubmit={() => valid && onNext()}
      footer={
        <button type="submit" className="oobe-primary" disabled={!valid}>
          Continue
        </button>
      }
    >
      <div className="oobe-group">
        <div className="oobe-row">
          <label htmlFor="oobe-fullname">Full name</label>
          <input
            id="oobe-fullname"
            className="oobe-input"
            type="text"
            value={fullName}
            onChange={(e) => onFullName(e.target.value)}
            autoComplete="name"
            autoCapitalize="words"
            placeholder="Required"
            autoFocus
          />
        </div>
        <div className="oobe-row">
          {invite ? (
            <>
              <span className="oobe-row-label" id="oobe-username-label">Username</span>
              <div
                className="oobe-readonly"
                role="textbox"
                aria-readonly="true"
                aria-labelledby="oobe-username-label"
              >
                {username}
              </div>
            </>
          ) : (
            <>
              <label htmlFor="oobe-username">Username</label>
              <input
                id="oobe-username"
                className="oobe-input"
                type="text"
                value={username}
                onChange={(e) => onUsername(cleanUsername(e.target.value))}
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="Required"
                aria-describedby="oobe-username-note"
              />
            </>
          )}
        </div>
      </div>
      {!invite && (
        <p className="oobe-note" id="oobe-username-note">
          You sign in to this device with your username.
        </p>
      )}
    </OobePage>
  );
}
